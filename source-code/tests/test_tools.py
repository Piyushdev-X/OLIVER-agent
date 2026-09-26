import os

from oliver import tools
from support import init_tools, make_repo

SOURCE = 'def add(a, b):\n    return a - b\n\n\ndef twice(x):\n    return add(x, x)\n'


def setup_repo(tmp_path):
    repo = make_repo(tmp_path, {'calc.py': SOURCE, 'docs/readme.md': 'calc docs\n'})
    init_tools(repo)
    return repo


def test_read_file_numbers_lines_and_windows(tmp_path):
    setup_repo(tmp_path)
    output = tools.read_file('calc.py', 2, 2)
    assert 'lines 2-2 of 6' in output
    assert '2\t    return a - b' in output


def test_paths_outside_the_repository_are_refused(tmp_path):
    repo = setup_repo(tmp_path)
    outside = tmp_path.parent / 'outside.txt'
    outside.write_text('secret')
    assert tools.read_file('../outside.txt').startswith('ERROR')
    assert tools.read_file(str(outside)).startswith('ERROR')
    os.symlink(str(outside), os.path.join(repo, 'link.txt'))
    assert tools.read_file('link.txt').startswith('ERROR')
    assert tools.write_file('../escape.py', 'x = 1\n').startswith('ERROR')
    assert tools.read_file('.git/config').startswith('ERROR')


def test_edit_file_requires_a_unique_exact_match(tmp_path):
    repo = setup_repo(tmp_path)
    assert 'Edited calc.py' in tools.edit_file('calc.py', 'return a - b', 'return a + b')
    assert 'return a + b' in open(os.path.join(repo, 'calc.py')).read()
    missing = tools.edit_file('calc.py', 'return a * b', 'return 0')
    assert missing.startswith('ERROR') and 'closest match' in missing
    make_repo(tmp_path, {'dup.py': 'x = 1\nx = 1\n'})
    assert 'matches 2 places' in tools.edit_file('dup.py', 'x = 1', 'x = 2')


def test_edits_that_break_syntax_are_rolled_back(tmp_path):
    repo = setup_repo(tmp_path)
    result = tools.edit_file('calc.py', 'return a - b', 'return (a - b')
    assert result.startswith('ERROR') and 'rolled back' in result
    assert open(os.path.join(repo, 'calc.py')).read() == SOURCE
    assert tools.write_file('new.py', 'def broken(:\n').startswith('ERROR')
    assert not os.path.exists(os.path.join(repo, 'new.py'))


def test_write_file_creates_directories(tmp_path):
    repo = setup_repo(tmp_path)
    assert tools.write_file('pkg/sub/mod.py', 'VALUE = 1\n').startswith('Created')
    assert os.path.isfile(os.path.join(repo, 'pkg', 'sub', 'mod.py'))


def test_search_repo_reports_definitions_and_matches(tmp_path):
    setup_repo(tmp_path)
    output = tools.search_repo('add')
    assert 'Symbol definitions' in output and 'calc.py:1' in output
    assert 'Text matches' in output


def test_run_command_blocks_risky_commands_and_hides_secrets(tmp_path, monkeypatch):
    setup_repo(tmp_path)
    assert tools.run_command('git push origin main').startswith('ERROR')
    assert tools.run_command('curl https://example.com').startswith('ERROR')
    assert tools.run_command('pip install requests').startswith('ERROR')
    monkeypatch.setenv('FAKE_SERVICE_API_KEY', 'sk-test-value')
    output = tools.run_command('echo "value=$FAKE_SERVICE_API_KEY"')
    assert 'exit code 0' in output and 'sk-test-value' not in output


def test_dispatch_validates_arguments(tmp_path):
    setup_repo(tmp_path)
    assert not tools.dispatch_tool_call('nope', {})['ok']
    bad_type = tools.dispatch_tool_call('read_file', {'filepath': 'calc.py', 'start_line': 'abc'})
    assert not bad_type['ok'] and 'invalid arguments' in bad_type['output']
    unexpected = tools.dispatch_tool_call('read_file', {'filepath': 'calc.py', 'colour': 'red'})
    assert not unexpected['ok']
    assert tools.dispatch_tool_call('finish', {'summary': 'done'})['ok']
    assert tools.TOOL_CONTEXT['finished']
