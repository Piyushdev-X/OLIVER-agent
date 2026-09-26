"""Verification for OLIVER: run commands and tests, compare against the baseline.

The harness, not the model, decides whether a change is verified:
  1. every changed file must parse,
  2. the plan's reproduction command (if any) must now succeed,
  3. no test that passed at baseline may fail now,
  4. tests the task is about (named in the plan, or in files the agent touched)
     must pass, and new tests the agent added must pass.
"""

import json
import os
import re
import shlex
import signal
import subprocess
import time
import uuid
import xml.etree.ElementTree as ElementTree

from oliver import context_manager

SECRET_MARKERS = ['KEY', 'TOKEN', 'SECRET', 'PASSWORD', 'CREDENTIAL', 'AUTH']
MAX_CAPTURE_CHARS = 400000
FAILING = ['failed', 'error']
SUITE_ID = '(test suite)'
CANNOT_RUN_EXIT_CODES = [126, 127]

BLOCKED_COMMANDS = [
    (re.compile(r'\bgit\s+(push|commit|reset|checkout|switch|restore|rebase|merge|clean|stash|am|apply)\b'),
     'git commands that change history or the working tree are managed by the harness'),
    (re.compile(r'\b(curl|wget|ssh|scp|rsync|nc|ncat|telnet)\b'), 'network access is not available'),
    (re.compile(r'\b(pip|pip3|npm|yarn|pnpm|apt|apt-get|brew|conda)\s+(install|add|i)\b'),
     'installing packages is not allowed; work with the dependencies already installed'),
    (re.compile(r'\bsudo\b'), 'sudo is not allowed'),
    (re.compile(r'\b(shutdown|reboot|mkfs|dd)\b'), 'system commands are not allowed'),
    (re.compile(r'\brm\s+-[a-zA-Z]*r[a-zA-Z]*\s+(/|~|\$HOME|\*)(\s|$)'), 'recursive deletes outside the project are not allowed'),
    (re.compile(r':\(\)\s*\{'), 'fork bombs are not allowed'),
]


def blocked_reason(command):
    for pattern, reason in BLOCKED_COMMANDS:
        if pattern.search(command):
            return reason
    return ''


# --- command execution -------------------------------------------------------

def scrubbed_environment():
    """Child processes never see API keys or tokens, so model-written commands
    cannot print or exfiltrate them."""
    env = {}
    for name in os.environ:
        upper = name.upper()
        if any(marker in upper for marker in SECRET_MARKERS):
            continue
        env[name] = os.environ[name]
    env['PYTHONDONTWRITEBYTECODE'] = '1'
    env['PYTHONUNBUFFERED'] = '1'
    env['PAGER'] = 'cat'
    env['GIT_PAGER'] = 'cat'
    env['CI'] = '1'
    return env


def command_result(command, exit_code, stdout, stderr, timed_out, started):
    return {
        'command': command,
        'exit_code': exit_code,
        'stdout': stdout[-MAX_CAPTURE_CHARS:],
        'stderr': stderr[-MAX_CAPTURE_CHARS:],
        'timed_out': timed_out,
        'duration': round(time.monotonic() - started, 3),
    }


def execute_command(command, cwd, timeout_seconds, settings):
    if settings['sandbox'] == 'docker':
        return execute_in_docker(command, cwd, timeout_seconds, settings)
    return execute_locally(command, cwd, timeout_seconds)


def execute_locally(command, cwd, timeout_seconds):
    started = time.monotonic()
    try:
        process = subprocess.Popen(['bash', '-c', command], cwd=cwd, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, stdin=subprocess.DEVNULL,
                                   env=scrubbed_environment(), start_new_session=True)
    except OSError as error:
        return command_result(command, 127, '', 'failed to start: ' + str(error), False, started)
    timed_out = False
    try:
        out, err = process.communicate(timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        timed_out = True
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except OSError:
            process.kill()
        out, err = process.communicate()
    exit_code = process.returncode if not timed_out else 124
    return command_result(command, exit_code, out.decode('utf-8', 'replace'),
                          err.decode('utf-8', 'replace'), timed_out, started)


def execute_in_docker(command, cwd, timeout_seconds, settings):
    """Run inside a throwaway container: repository mounted at /workspace,
    networking disabled, memory and CPU capped."""
    started = time.monotonic()
    try:
        import docker
        client = docker.from_env()
        container = client.containers.run(
            settings['docker_image'], ['bash', '-c', command],
            volumes={os.path.abspath(cwd): {'bind': '/workspace', 'mode': 'rw'}},
            working_dir='/workspace', network_disabled=True, mem_limit='2g',
            nano_cpus=2000000000, detach=True)
    except Exception as error:
        return command_result(command, 125, '', 'docker sandbox unavailable: ' + str(error), False, started)
    timed_out = False
    exit_code = 1
    try:
        outcome = container.wait(timeout=timeout_seconds)
        exit_code = outcome['StatusCode'] if 'StatusCode' in outcome else 1
    except Exception:
        timed_out = True
        exit_code = 124
        try:
            container.kill()
        except Exception:
            pass
    try:
        out = container.logs(stdout=True, stderr=False).decode('utf-8', 'replace')
        err = container.logs(stdout=False, stderr=True).decode('utf-8', 'replace')
    finally:
        try:
            container.remove(force=True)
        except Exception:
            pass
    return command_result(command, exit_code, out, err, timed_out, started)


# --- test discovery ----------------------------------------------------------

def detect_test_command(repo_root, index):
    files = index['files']
    python_tests = [rel for rel in files if rel.endswith('.py') and context_manager.is_test_path(rel)]
    python_config = any(os.path.basename(rel) in ('pytest.ini', 'conftest.py', 'tox.ini') for rel in files)
    if python_tests or python_config:
        return 'python3 -m pytest'
    package_json = os.path.join(repo_root, 'package.json')
    if os.path.isfile(package_json):
        try:
            with open(package_json, 'r', encoding='utf-8') as handle:
                package = json.load(handle)
            scripts = package['scripts'] if 'scripts' in package else {}
            if 'test' in scripts and 'no test specified' not in scripts['test']:
                return 'npm test --silent'
        except (OSError, ValueError):
            pass
    if os.path.isfile(os.path.join(repo_root, 'go.mod')):
        return 'go test ./...'
    if os.path.isfile(os.path.join(repo_root, 'Cargo.toml')):
        return 'cargo test'
    makefile = os.path.join(repo_root, 'Makefile')
    if os.path.isfile(makefile):
        try:
            with open(makefile, 'r', encoding='utf-8', errors='replace') as handle:
                if any(line.startswith('test:') for line in handle):
                    return 'make test'
        except OSError:
            pass
    return ''


def is_pytest_command(test_command):
    return 'pytest' in test_command


# --- running tests -----------------------------------------------------------

def run_tests(repo_root, test_command, targets, settings):
    """Run the test command and return per-test outcomes when pytest is used."""
    if not test_command:
        return None
    junit_name = '.oliver-junit-' + uuid.uuid4().hex[:8] + '.xml'
    command = test_command
    if is_pytest_command(test_command):
        command += ' -q -rfE -p no:cacheprovider --junitxml=' + junit_name
    if targets:
        command += ' ' + ' '.join(quote_argument(target) for target in targets)
    result = execute_command(command, repo_root, settings['test_timeout'], settings)
    junit_path = os.path.join(repo_root, junit_name)
    outcomes = {}
    details = {}
    if os.path.isfile(junit_path):
        outcomes, details = parse_junit(junit_path)
        try:
            os.remove(junit_path)
        except OSError:
            pass
    ran = result['exit_code'] not in CANNOT_RUN_EXIT_CODES and not result['timed_out']
    if not outcomes and not is_pytest_command(test_command) and ran:
        outcomes = {SUITE_ID: 'passed' if result['exit_code'] == 0 else 'failed'}
    counts = {'passed': 0, 'failed': 0, 'error': 0, 'skipped': 0}
    for status in outcomes.values():
        counts[status] += 1
    return {
        'command': command,
        'exit_code': result['exit_code'],
        'timed_out': result['timed_out'],
        'duration': result['duration'],
        'outcomes': outcomes,
        'details': details,
        'counts': counts,
        'collected': bool(outcomes),
        'output': context_manager.truncate_output(result['stdout'] + '\n' + result['stderr'], 6000),
    }


def quote_argument(text):
    return shlex.quote(text)


def parse_junit(path):
    outcomes = {}
    details = {}
    try:
        document = ElementTree.parse(path)
    except (ElementTree.ParseError, OSError):
        return outcomes, details
    for case in document.iter('testcase'):
        attributes = case.attrib
        name = attributes['name'] if 'name' in attributes else '?'
        classname = attributes['classname'] if 'classname' in attributes else ''
        test_id = junit_test_id(classname, name, attributes)
        status = 'passed'
        message = ''
        for tag in ('failure', 'error', 'skipped'):
            element = case.find(tag)
            if element is not None:
                status = {'failure': 'failed', 'error': 'error', 'skipped': 'skipped'}[tag]
                element_attributes = element.attrib
                message = element_attributes['message'] if 'message' in element_attributes else ''
                if element.text:
                    message = (message + '\n' + element.text).strip()
                break
        outcomes[test_id] = status
        if message:
            details[test_id] = message[-3000:]
    return outcomes, details


def junit_test_id(classname, name, attributes):
    """tests.test_stats + test_average -> tests/test_stats.py::test_average."""
    if 'file' in attributes and attributes['file']:
        return attributes['file'] + '::' + name
    if not classname:
        return name
    parts = classname.split('.')
    module_parts = []
    class_parts = []
    for part in parts:
        if class_parts or (part[:1].isupper() and module_parts):
            class_parts.append(part)
        else:
            module_parts.append(part)
    test_id = '/'.join(module_parts) + '.py'
    for part in class_parts:
        test_id += '::' + part
    return test_id + '::' + name


def summarize_tests(results):
    if results is None:
        return 'no test command detected'
    counts = results['counts']
    if not results['collected']:
        return 'no tests collected (exit code {0})'.format(results['exit_code'])
    failing = [test for test in sorted(results['outcomes']) if results['outcomes'][test] in FAILING]
    text = '{0} passed, {1} failed, {2} errors, {3} skipped'.format(
        counts['passed'], counts['failed'], counts['error'], counts['skipped'])
    if failing:
        text += '; failing: ' + ', '.join(failing[:8]) + (' ...' if len(failing) > 8 else '')
    return text


def compare_to_baseline(baseline, final):
    """Without a baseline run, a failing test has unknown provenance: it counts as
    still failing (and is judged by whether it relates to the task), not as new."""
    known = baseline is not None
    base = baseline['outcomes'] if known else {}
    current = final['outcomes'] if final else {}
    comparison = {'fixed': [], 'broken': [], 'still_failing': [], 'new_passing': [], 'new_failing': [],
                  'missing': []}
    for test in sorted(current):
        now_failing = current[test] in FAILING
        if test not in base:
            if now_failing and not known and test != SUITE_ID:
                comparison['still_failing'].append(test)
            else:
                comparison['new_failing' if now_failing else 'new_passing'].append(test)
            continue
        before_failing = base[test] in FAILING
        if before_failing and not now_failing:
            comparison['fixed'].append(test)
        elif not before_failing and now_failing:
            comparison['broken'].append(test)
        elif before_failing and now_failing:
            comparison['still_failing'].append(test)
    for test in sorted(base):
        if test not in current and base[test] not in FAILING:
            comparison['missing'].append(test)
    return comparison


# --- syntax ------------------------------------------------------------------

def check_syntax(path):
    """Return '' when the file parses, else a short error message."""
    extension = os.path.splitext(path)[1].lower()
    if extension not in ('.py', '.json'):
        return ''
    try:
        with open(path, 'rb') as handle:
            source = handle.read()
    except OSError as error:
        return 'cannot read file: ' + str(error)
    if extension == '.json':
        try:
            json.loads(source)
        except ValueError as error:
            return 'invalid JSON: ' + str(error)
        return ''
    try:
        compile(source, path, 'exec')
    except SyntaxError as error:
        return 'line {0}: {1}'.format(error.lineno, error.msg)
    except ValueError as error:
        return str(error)
    return ''


# --- the verification pipeline -----------------------------------------------

def add_check(checks, name, ok, detail):
    checks.append({'name': name, 'ok': ok, 'detail': detail})


def node_matches(test_id, node):
    """A plan may name a node id (file::test, file::Class, possibly without the
    directory); parametrized ids carry a [suffix]."""
    padded = '/' + test_id
    if padded.endswith('/' + node):
        return True
    return ('/' + node + '::') in padded or ('/' + node + '[') in padded


def test_belongs_to(test_id, paths):
    """True when test_id is in one of the files, or is one of the node ids, in paths."""
    test_file = test_id.split('::')[0]
    for path in paths:
        path = path[2:] if path.startswith('./') else path
        if '::' in path:
            if node_matches(test_id, path):
                return True
            continue
        if test_file == path or test_file.endswith('/' + path) or path.endswith('/' + test_file):
            return True
    return False


def verify_changes(repo_root, changed, baseline, test_command, plan, settings):
    """changed: list of {'status', 'path'} relative to the baseline snapshot."""
    started = time.monotonic()
    checks = []
    warnings = []
    final = None
    comparison = None
    if not changed:
        add_check(checks, 'changes', False, 'no files were changed')
        return verification_result(checks, warnings, final, comparison, started)
    add_check(checks, 'changes', True, ', '.join(item['path'] for item in changed[:20]))

    syntax_errors = []
    for item in changed:
        if item['status'] == 'D':
            continue
        error = check_syntax(os.path.join(repo_root, item['path']))
        if error:
            syntax_errors.append(item['path'] + ': ' + error)
    add_check(checks, 'syntax', not syntax_errors, '\n'.join(syntax_errors) if syntax_errors else 'all changed files parse')
    if syntax_errors:
        return verification_result(checks, warnings, final, comparison, started)

    for item in changed:
        if item['status'] in ('M', 'D') and context_manager.is_test_path(item['path']):
            warnings.append('existing test file changed: ' + item['path'])

    reproduction = plan['reproduction_command'] if plan and 'reproduction_command' in plan else ''
    refused = blocked_reason(reproduction) if reproduction else ''
    if refused:
        warnings.append('reproduction command skipped (' + refused + '): ' + reproduction[:200])
    elif reproduction:
        result = execute_command(reproduction, repo_root, settings['command_timeout'], settings)
        ok = result['exit_code'] == 0 and not result['timed_out']
        detail = 'exit code {0}{1}\n{2}'.format(
            result['exit_code'], ' (timed out)' if result['timed_out'] else '',
            context_manager.truncate_output((result['stdout'] + '\n' + result['stderr']).strip(), 3000))
        add_check(checks, 'reproduction', ok, detail)

    if test_command:
        final = run_tests(repo_root, test_command, [], settings)
        comparison = compare_to_baseline(baseline, final)
        touched = [item['path'] for item in changed]
        named = plan['tests_to_run'] if plan and 'tests_to_run' in plan else []
        related_failing = [test for test in comparison['still_failing']
                           if test_belongs_to(test, touched) or test_belongs_to(test, named)]
        problems = []
        if final['timed_out']:
            problems.append('test run timed out after {0}s'.format(settings['test_timeout']))
        if comparison['broken']:
            problems.append('broken (passed before, fail now): ' + ', '.join(comparison['broken'][:10]))
        if comparison['missing']:
            problems.append('no longer collected: ' + ', '.join(comparison['missing'][:10]))
        if comparison['new_failing']:
            problems.append('new tests failing: ' + ', '.join(comparison['new_failing'][:10]))
        if related_failing:
            problems.append('task-related tests still failing: ' + ', '.join(related_failing[:10]))
        if not final['collected'] and not final['timed_out']:
            if final['exit_code'] not in (0, 5):
                problems.append('tests could not run (exit code {0}), so there is no test evidence'.format(
                    final['exit_code']))
            elif baseline and baseline['collected']:
                problems.append('tests could not be collected (exit code {0})'.format(final['exit_code']))
            else:
                warnings.append('no per-test results were collected; verification relies on the other checks')
        unrelated = [test for test in comparison['still_failing'] if test not in related_failing]
        if unrelated:
            warnings.append('pre-existing failures left unchanged: ' + ', '.join(unrelated[:10]))
        summary = summarize_tests(final)
        if comparison['fixed']:
            summary += '; fixed: ' + ', '.join(comparison['fixed'][:10])
        add_check(checks, 'tests', not problems, '\n'.join(problems) if problems else summary)
    return verification_result(checks, warnings, final, comparison, started)


def verification_result(checks, warnings, final, comparison, started):
    return {
        'passed': all(check['ok'] for check in checks),
        'checks': checks,
        'warnings': warnings,
        'tests': final,
        'comparison': comparison,
        'duration': round(time.monotonic() - started, 3),
    }


def describe_verification(result):
    lines = []
    for check in result['checks']:
        label = ('PASS ' if check['ok'] else 'FAIL ') + check['name']
        first_line = check['detail'].splitlines()[0] if check['detail'] else ''
        lines.append(label + (': ' + first_line if first_line else ''))
    for warning in result['warnings']:
        lines.append('WARN ' + warning)
    return '\n'.join(lines)
