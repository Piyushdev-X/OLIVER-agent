"""The submission requirements: Makefile interface, AI_API_KEY handling, model
configuration, the evaluation console, and a repository with nothing extra."""

import os
import re
import shutil
import subprocess

from oliver import config
from oliver import console
from oliver import foundation_model
from oliver import orchestrator
from oliver import report
from oliver import verification
from support import REPO_ROOT, TASKS_DIR, copy_task

FAKE_ANTHROPIC_KEY = 'sk-ant-' + 'k' * 40
ALLOWED_TOP_LEVEL = ['.env.example', '.gitignore', 'Makefile', 'README.md', 'configuration-files',
                     'dependency-files', 'source-code']


def write_config(tmp_path, text):
    path = tmp_path / 'oliver.toml'
    path.write_text(text)
    return str(path)


def isolate_environment(monkeypatch, key):
    monkeypatch.setenv('AI_API_KEY', key)
    for name in ('OLIVER_MODEL', 'OLIVER_API_BASE', 'OLIVER_CONFIG'):
        monkeypatch.delenv(name, raising=False)


def scripted_reader(answers):
    queue = list(answers)

    def read(prompt):
        if not queue:
            raise EOFError
        return queue.pop(0)
    return read


# --- configuration ------------------------------------------------------------

def test_configuration_file_defines_the_model_and_every_setting():
    overrides, auto_models = config.read_config_file(config.DEFAULT_CONFIG)
    expected = set(orchestrator.DEFAULT_SETTINGS) - set(config.NOT_IN_FILE)
    assert set(overrides) == expected
    assert overrides['model'] and overrides['temperature'] == 0.0 and overrides['seed'] >= 0
    assert auto_models and all('/' in model for model in auto_models.values())


def test_unknown_or_mistyped_settings_are_rejected(tmp_path):
    for text, fragment in (('modle = "x"\n', 'unknown setting'), ('max_steps = "ten"\n', 'wrong type'),
                           ('review = 1\n', 'wrong type')):
        try:
            config.read_config_file(write_config(tmp_path, text))
        except ValueError as error:
            assert fragment in str(error)
        else:
            raise AssertionError('accepted: ' + text)


def test_auto_model_follows_the_provider_of_the_key(tmp_path, monkeypatch):
    isolate_environment(monkeypatch, FAKE_ANTHROPIC_KEY)
    path = write_config(tmp_path, 'model = "auto"\n[auto_models]\nanthropic = "anthropic/claude-sonnet-5"\n')
    settings, info = config.load_settings(path, {})
    assert settings['model'] == 'anthropic/claude-sonnet-5' and not info['problem']
    assert info['key_provider'] == 'anthropic'
    assert FAKE_ANTHROPIC_KEY not in repr(settings) + repr(info)


def test_missing_or_mismatched_credentials_are_reported(tmp_path, monkeypatch):
    isolate_environment(monkeypatch, '')
    settings, info = config.load_settings(write_config(tmp_path, 'model = "auto"\n'), {})
    assert 'AI_API_KEY' in info['problem']
    isolate_environment(monkeypatch, FAKE_ANTHROPIC_KEY)
    settings, info = config.load_settings(write_config(tmp_path, 'model = "openai/gpt-4.1"\n'), {})
    assert not info['problem'] and 'anthropic key' in info['warnings'][0]
    monkeypatch.setenv('OLIVER_MODEL', 'anthropic/claude-sonnet-5')
    settings, info = config.load_settings(write_config(tmp_path, 'model = "openai/gpt-4.1"\n'), {})
    assert settings['model'] == 'anthropic/claude-sonnet-5' and info['model_source'] == 'OLIVER_MODEL'


# --- the credential ----------------------------------------------------------

def test_credential_is_redacted_from_errors_and_evidence(tmp_path, monkeypatch):
    isolate_environment(monkeypatch, FAKE_ANTHROPIC_KEY)
    error = foundation_model.error_response('fatal', 'Invalid key ' + FAKE_ANTHROPIC_KEY + ' for model')
    assert FAKE_ANTHROPIC_KEY not in error['error_message'] and '[AI_API_KEY]' in error['error_message']
    repo, task = copy_task('stats_average', tmp_path)
    settings = {'model': 'mock:' + os.path.join(TASKS_DIR, 'stats_average', 'mock_script.json')}
    result = orchestrator.run_agent_loop(task + '\nleaked ' + FAKE_ANTHROPIC_KEY, repo, settings)
    run_dir = report.write_run_artifacts(result, str(tmp_path / 'runs'))
    for name in report.EVIDENCE_FILES:
        with open(os.path.join(run_dir, name), 'r', encoding='utf-8') as handle:
            assert FAKE_ANTHROPIC_KEY not in handle.read()


def test_commands_never_see_the_key_or_the_harness_source(monkeypatch):
    isolate_environment(monkeypatch, FAKE_ANTHROPIC_KEY)
    other = os.path.join(os.sep, 'opt', 'libraries')
    monkeypatch.setenv('PYTHONPATH', os.pathsep.join([verification.HARNESS_SOURCE, other]))
    env = verification.scrubbed_environment()
    assert 'AI_API_KEY' not in env and env['PYTHONPATH'] == other


# --- the evaluation console ----------------------------------------------------

def console_settings(task_name):
    return orchestrator.build_settings({'model': 'mock:' + os.path.join(TASKS_DIR, task_name, 'mock_script.json'),
                                        'verbose': True})


def console_info():
    return {'config_path': config.DEFAULT_CONFIG, 'configured_model': 'mock', 'model_source': 'test',
            'key_set': False, 'key_provider': '', 'problem': '', 'warnings': []}


def console_arguments(tmp_path, repo='', issue=''):
    return {'repo': repo, 'issue': issue, 'out': str(tmp_path / 'runs')}


def test_console_solves_a_pasted_issue_from_piped_input(tmp_path, capsys):
    repo, task = copy_task('stats_average', tmp_path)
    issue_lines = task.strip().splitlines()
    answers = [repo] + issue_lines + ['END', '', 'n']
    args = console_arguments(tmp_path)
    code = console.run_console(args, console_settings('stats_average'), console_info(),
                               reader=scripted_reader(answers))
    output = capsys.readouterr().out
    assert code == 0
    assert 'evaluation mode' in output and 'Result: RESOLVED' in output and 'OLIVER stopped.' in output
    assert '[OLIVER' in output and 'tool edit_file' in output
    with open(os.path.join(repo, 'stats.py'), 'r', encoding='utf-8') as handle:
        assert 'return total(values) / len(values)' in handle.read()
    assert len(os.listdir(args['out'])) == 1


def test_console_accepts_an_issue_file_and_stops_at_end_of_input(tmp_path, capsys):
    repo, _ = copy_task('leap_year', tmp_path)
    issue_file = os.path.join(TASKS_DIR, 'leap_year', 'issue.md')
    answers = [os.path.join(tmp_path, 'missing'), repo, issue_file]
    code = console.run_console(console_arguments(tmp_path), console_settings('leap_year'), console_info(),
                               reader=scripted_reader(answers))
    output = capsys.readouterr().out
    assert code == 0 and 'Repository not found' in output and 'Result: RESOLVED' in output
    assert console.run_console(console_arguments(tmp_path), console_settings('leap_year'), console_info(),
                               reader=scripted_reader([])) == 0


def test_console_refuses_to_edit_the_harness_and_reports_a_missing_key(tmp_path, capsys):
    for path in (config.PROJECT_ROOT, config.SOURCE_ROOT, os.path.dirname(config.PROJECT_ROOT)):
        try:
            console.prepare_repository(path)
        except ValueError as error:
            assert 'OLIVER harness' in str(error)
        else:
            raise AssertionError('accepted ' + path)
    info = console_info()
    info['problem'] = 'AI_API_KEY is not set'
    assert console.run_console(console_arguments(tmp_path), console_settings('leap_year'), info,
                               reader=scripted_reader([])) == 2
    assert 'Cannot start: AI_API_KEY is not set' in capsys.readouterr().out


def test_cloned_repositories_never_overwrite_each_other(tmp_path):
    origin = copy_task('word_count', tmp_path)[0]
    for command in (['git', 'init', '-q'], ['git', 'add', '-A'],
                    ['git', '-c', 'user.name=t', '-c', 'user.email=t@t', 'commit', '-qm', 'init']):
        subprocess.run(command, cwd=origin, check=True, capture_output=True)
    workspace_existed = os.path.isdir(config.WORKSPACE_DIR)
    clones = [console.prepare_repository('file://' + origin) for _ in range(3)]
    try:
        assert len(set(clones)) == 3
        assert all(os.path.isfile(os.path.join(clone, 'text_stats.py')) for clone in clones)
        try:
            console.prepare_repository('file://' + str(tmp_path / 'missing'))
        except ValueError as error:
            assert 'git failed' in str(error)
        assert all(os.path.isdir(clone) for clone in clones)
    finally:
        for clone in clones:
            shutil.rmtree(clone)
        if not workspace_existed and not os.listdir(config.WORKSPACE_DIR):
            os.rmdir(config.WORKSPACE_DIR)


def test_issue_input_forms():
    assert console.resolve_issue('  Fix the parser  ') == 'Fix the parser'
    issue_file = os.path.join(TASKS_DIR, 'word_count', 'issue.md')
    with open(issue_file, 'r', encoding='utf-8') as handle:
        assert console.resolve_issue(issue_file) == handle.read().strip()
    assert console.read_issue(scripted_reader(['second line', 'END']), 'first line') == 'first line\nsecond line'
    assert console.GITHUB_ISSUE.match('https://github.com/owner/repo/issues/42').groups() == ('owner', 'repo', '42')


# --- the Makefile interface and the repository ----------------------------------

def read_repo_file(name):
    with open(os.path.join(REPO_ROOT, name), 'r', encoding='utf-8') as handle:
        return handle.read()


def test_makefile_exposes_the_standard_interface_without_echoing_the_key():
    makefile = read_repo_file('Makefile')
    targets = re.findall(r'^([a-z]+):', makefile, re.MULTILINE)
    for target in ('setup', 'run', 'test', 'clean'):
        assert target in targets
    for line in makefile.splitlines():
        if line.startswith('\t') and 'AI_API_KEY' in line and 'echo' not in line:
            assert line.startswith('\t@'), 'recipe line would print the key: ' + line
    assert '$(AI_API_KEY)' not in makefile


def test_credentials_are_only_examples():
    assert read_repo_file('.env.example').strip().splitlines()[-1] == 'AI_API_KEY='
    assert '.env' in read_repo_file('.gitignore').splitlines()


def test_repository_holds_only_the_submission_layout():
    listed = subprocess.run(['git', 'ls-files'], cwd=REPO_ROOT, capture_output=True, text=True)
    if listed.returncode != 0 or not listed.stdout.strip():
        return
    top_level = sorted(set(path.split('/')[0] for path in listed.stdout.splitlines()))
    assert [entry for entry in top_level if entry not in ALLOWED_TOP_LEVEL] == []
