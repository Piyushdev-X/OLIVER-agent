import json
import os
import subprocess
import sys

from oliver import main as cli
from oliver import orchestrator
from oliver import report
from support import PROJECT_ROOT, TASKS_DIR, copy_task

CHECKER = os.path.join(PROJECT_ROOT, 'scripts', 'check_constraints.py')


def mock_settings(task_name, extra=None):
    settings = {'model': 'mock:' + os.path.join(TASKS_DIR, task_name, 'mock_script.json')}
    settings.update(extra if extra else {})
    return settings


def write_script(tmp_path, script):
    path = tmp_path / 'script.json'
    path.write_text(json.dumps(script))
    return 'mock:' + str(path)


def test_success_path_resolves_and_patches(tmp_path):
    repo, task = copy_task('stats_average', tmp_path)
    result = orchestrator.run_agent_loop(task, repo, mock_settings('stats_average'))
    assert result['status'] == 'resolved' and result['final_phase'] == 'DONE'
    assert '+    return total(values) / len(values)' in result['patch']
    assert result['verification']['passed'] and result['attempts'] == 0


def test_regression_goes_through_recovery(tmp_path):
    repo, task = copy_task('inventory_total', tmp_path)
    result = orchestrator.run_agent_loop(task, repo, mock_settings('inventory_total'))
    assert result['status'] == 'resolved' and result['attempts'] == 1
    assert any(entry['kind'] == 'recover' and entry['detail'].startswith('regression')
               for entry in result['trajectory'])


def test_syntax_guard_keeps_the_file_valid(tmp_path):
    repo, task = copy_task('text_slugify', tmp_path)
    result = orchestrator.run_agent_loop(task, repo, mock_settings('text_slugify'))
    failed_edits = [e for e in result['trajectory'] if e['kind'] == 'tool' and not e['ok']]
    assert failed_edits and 'rolled back' in failed_edits[0]['detail']
    assert result['status'] == 'resolved'


def test_step_limit_ends_in_failed_without_a_patch(tmp_path):
    repo, task = copy_task('stats_average', tmp_path)
    result = orchestrator.run_agent_loop(task, repo, mock_settings('stats_average', {'max_steps': 2}))
    assert result['status'] == 'failed' and 'step limit' in result['stop_reason']


def test_repeated_failure_triggers_rollback_and_replan(tmp_path):
    repo, task = copy_task('stats_average', tmp_path)
    wrong = {'tool_calls': [{'name': 'edit_file', 'arguments': {
        'filepath': 'stats.py', 'old_text': 'return 0.0', 'new_text': 'return 1.0'}}]}
    finish = {'tool_calls': [{'name': 'finish', 'arguments': {'summary': 'wrong'}}]}
    script = {'execute': [wrong, finish, finish, wrong, finish, finish, finish]}
    settings = {'model': write_script(tmp_path, script), 'max_recovery_attempts': 3, 'review': False}
    result = orchestrator.run_agent_loop(task, repo, settings)
    assert result['replans'] == 1
    assert result['status'] in ('unverified', 'failed')
    assert any(entry['kind'] == 'replan' for entry in result['trajectory'])


def test_context_overflow_is_retried_after_compression(tmp_path):
    repo, task = copy_task('stats_average', tmp_path)
    script = json.load(open(os.path.join(TASKS_DIR, 'stats_average', 'mock_script.json')))
    script['plan'].insert(0, {'error': 'context_overflow'})
    result = orchestrator.run_agent_loop(task, repo, {'model': write_script(tmp_path, script)})
    assert result['status'] == 'resolved'
    assert any(entry['kind'] == 'overflow' for entry in result['trajectory'])


def test_cli_writes_the_evidence_bundle(tmp_path):
    repo, _ = copy_task('leap_year', tmp_path)
    out = tmp_path / 'runs'
    code = cli.main(['--repo', repo, '--task-file', os.path.join(TASKS_DIR, 'leap_year', 'issue.md'),
                     '--model', 'mock:' + os.path.join(TASKS_DIR, 'leap_year', 'mock_script.json'),
                     '--out', str(out), '--quiet'])
    assert code == 0
    run_dir = os.path.join(str(out), os.listdir(str(out))[0])
    for name in ('patch.diff', 'trajectory.jsonl', 'verification.json', 'result.json', 'summary.md', 'report.html'):
        assert os.path.isfile(os.path.join(run_dir, name))
    assert cli.main(['--repo', repo, '--task', 'x', '--model', '']) == 2


def test_report_escapes_data_and_obeys_the_design_rules(tmp_path):
    repo, task = copy_task('word_count', tmp_path)
    result = orchestrator.run_agent_loop(task + '\n<script>alert(1)</script>', repo, mock_settings('word_count'))
    page = report.render_report(result)
    assert '<script>' not in page and '&lt;script&gt;' in page
    assert page.count('logo-slot') >= 6
    page_file = tmp_path / 'report.html'
    page_file.write_text(page)
    checked = subprocess.run([sys.executable, CHECKER, str(page_file)], capture_output=True, text=True)
    assert checked.returncode == 0, checked.stdout


def test_whole_repository_obeys_the_constraints():
    checked = subprocess.run([sys.executable, CHECKER], capture_output=True, text=True, cwd=PROJECT_ROOT)
    assert checked.returncode == 0, checked.stdout


def test_checker_catches_planted_violations(tmp_path):
    receiver = 's' + 'elf'
    getter = '.g' + 'et('
    bad = tmp_path / 'bad.py'
    bad.write_text('cla' + 'ss Foo:\n    pass\n\n\ndef f(d):\n    return d' + getter + "'k'), " + receiver + '.x\n')
    css = tmp_path / 'bad.css'
    css.write_text('.a { color: v' + 'ar(--x); }\n@me' + 'dia print { }\n')
    checked = subprocess.run([sys.executable, CHECKER, str(bad), str(css)], capture_output=True, text=True)
    assert checked.returncode == 1
    for rule in ('[R1]', '[R3]', '[R4]', '[R5]', '[R6]'):
        assert rule in checked.stdout
