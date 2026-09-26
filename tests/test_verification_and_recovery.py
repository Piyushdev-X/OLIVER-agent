import os

from oliver import context_manager
from oliver import orchestrator
from oliver import recovery
from oliver import verification
from support import make_repo

TESTS = ('from mod import value\n\n\ndef test_ok():\n    assert value() == 1\n\n\n'
         'def test_bad():\n    assert value() == 2\n')


def test_run_tests_parses_per_test_outcomes(tmp_path):
    repo = make_repo(tmp_path, {'mod.py': 'def value():\n    return 1\n', 'conftest.py': '',
                                'tests/test_mod.py': TESTS})
    index = context_manager.index_repository(repo)
    command = verification.detect_test_command(repo, index)
    assert command == 'python3 -m pytest'
    results = verification.run_tests(repo, command, [], orchestrator.build_settings({}))
    assert results['outcomes'] == {'tests/test_mod.py::test_ok': 'passed', 'tests/test_mod.py::test_bad': 'failed'}
    assert 'assert 1 == 2' in results['details']['tests/test_mod.py::test_bad']
    assert not [name for name in os.listdir(repo) if name.startswith('.oliver-junit')]


def test_compare_to_baseline_categories():
    baseline = {'outcomes': {'a': 'passed', 'b': 'failed', 'c': 'failed', 'd': 'passed'}}
    final = {'outcomes': {'a': 'failed', 'b': 'passed', 'c': 'failed', 'e': 'failed'}}
    comparison = verification.compare_to_baseline(baseline, final)
    assert comparison['broken'] == ['a'] and comparison['fixed'] == ['b']
    assert comparison['still_failing'] == ['c'] and comparison['new_failing'] == ['e']
    assert comparison['missing'] == ['d']


def test_command_timeout_and_syntax_check(tmp_path):
    result = verification.execute_locally('sleep 5', str(tmp_path), 1)
    assert result['timed_out'] and result['exit_code'] == 124
    make_repo(tmp_path, {'bad.py': 'def f(:\n', 'good.py': 'x = 1\n'})
    assert verification.check_syntax(str(tmp_path / 'bad.py')).startswith('line 1')
    assert verification.check_syntax(str(tmp_path / 'good.py')) == ''


def test_verify_changes_requires_a_change(tmp_path):
    result = verification.verify_changes(str(tmp_path), [], None, '', None, orchestrator.build_settings({}))
    assert not result['passed']
    assert recovery.classify_failure(result)['kind'] == 'no_changes'


def test_failure_classification_and_signatures():
    result = {'checks': [{'name': 'changes', 'ok': True, 'detail': 'x.py'},
                         {'name': 'tests', 'ok': False, 'detail': 'broken: t1'}],
              'warnings': [], 'comparison': {'broken': ['t1'], 'missing': [], 'fixed': [], 'new_failing': [],
                                             'still_failing': [], 'new_passing': []},
              'tests': {'timed_out': False, 'details': {'t1': 'E   AssertionError: expected 3 got 4'}, 'output': ''}}
    failure = recovery.classify_failure(result)
    assert failure['kind'] == 'regression'
    first = recovery.error_signature('regression', 'AssertionError: expected 3 got 4')
    second = recovery.error_signature('regression', 'AssertionError: expected 7 got 9')
    assert first.split(' ')[0] == second.split(' ')[0]
    assert recovery.should_replan([first, second])
    assert recovery.detect_loop(['a', 'b', 'b', 'b'], 3) and not recovery.detect_loop(['a', 'b', 'b'], 3)


def test_trim_traceback_drops_library_frames():
    text = '\n'.join(['File "/usr/lib/python3/site-packages/x.py", line 1'] * 50
                     + ['File "app.py", line 3', 'ValueError: bad input'])
    trimmed = recovery.trim_traceback(text, 10)
    assert 'site-packages' not in trimmed and 'ValueError: bad input' in trimmed


def test_snapshots_checkpoint_rollback_and_patch(tmp_path):
    repo = make_repo(tmp_path, {'a.py': 'x = 1\n'})
    snapshot = recovery.init_snapshots(repo)
    try:
        make_repo(tmp_path, {'a.py': 'x = 2\n', 'b.py': 'y = 1\n'})
        checkpoint = recovery.create_checkpoint(snapshot, 'cp')
        make_repo(tmp_path, {'c.py': 'z = 1\n'})
        recovery.restore_checkpoint(snapshot, checkpoint)
        assert not os.path.exists(os.path.join(repo, 'c.py'))
        assert [item['path'] for item in recovery.changed_files(snapshot)] == ['a.py', 'b.py']
        assert '+x = 2' in recovery.export_patch(snapshot)
        assert not os.path.exists(os.path.join(repo, '.git'))
    finally:
        recovery.cleanup_snapshots(snapshot)
