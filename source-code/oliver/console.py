"""OLIVER evaluation console: the session `make run` opens.

Plain text only (no colours, no full-screen drawing), so it behaves the same
in every terminal and also accepts piped input. For each task it asks for
  1. the repository: a local path, or a git URL that is cloned into workspace/
     (a GitHub issue URL also works and names both repository and issue),
  2. the issue or test case: pasted text ending with a line END (or Ctrl-D),
     the path of a text file, or a GitHub issue URL,
  3. optionally the command that runs the tests,
then runs the harness with live progress and prints the verdict, the patch
and where the evidence was written. The patch stays applied in the repository.
"""

import datetime
import json
import os
import re
import shutil
import subprocess
import urllib.request

from oliver import config
from oliver import foundation_model
from oliver import orchestrator
from oliver import report
from oliver.verification import describe_verification

RULE = '=' * 72
END_MARKER = 'END'
PATCH_PREVIEW_LINES = 120
GITHUB_ISSUE = re.compile(r'^https?://github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)/(?:issues|pull)/(\d+)')
GIT_URL = re.compile(r'^(https?://|ssh://|git://|file://|git@)')
VERDICTS = {
    'resolved': 'RESOLVED: the patch passed verification',
    'unverified': 'NOT VERIFIED: a patch was produced but did not pass verification',
    'failed': 'FAILED: no usable patch was produced',
}


# --- the session -------------------------------------------------------------

def run_console(options, settings, info, reader=None):
    """Interactive evaluation mode. options holds 'repo' and 'issue' (answers
    given on the command line, may be empty) and 'out' (the evidence directory).
    Returns the process exit code."""
    reader = reader if reader else input
    print_banner(settings, info)
    if info['problem']:
        print('Cannot start: ' + info['problem'] + '.')
        print('Export the credential (export AI_API_KEY="<PROVIDED_API_KEY>") and check the model in '
              + config.display_path(info['config_path']) + ', then run `make run` again.')
        return 2
    for warning in info['warnings']:
        print('Warning: ' + warning + '.')
    try:
        if not preflight(settings):
            return 2
        presets = {'repo': options['repo'], 'issue': options['issue']}
        while True:
            task = collect_task(reader, presets, settings['test_command'])
            if task is None:
                print('\nNo task received. OLIVER stopped.')
                return 0
            presets = {'repo': '', 'issue': ''}
            run_task(task, settings, options['out'])
            answer = ask(reader, '\nSolve another issue? [y/N] ')
            if answer is None or answer.strip().lower() not in ('y', 'yes'):
                print('OLIVER stopped.')
                return 0
    except KeyboardInterrupt:
        print('\nInterrupted. OLIVER stopped; the repository may hold partial edits.')
        return 130


def print_banner(settings, info):
    if info['problem'] and info['configured_model'] == config.AUTO_MODEL:
        model_line = 'auto (chosen from the AI_API_KEY provider)'
    else:
        model_line = settings['model'] + '   from ' + info['model_source']
    if not info['key_set']:
        key_line = 'AI_API_KEY is not set'
    elif info['key_provider']:
        key_line = 'AI_API_KEY is set (' + info['key_provider'] + ' key format)'
    else:
        key_line = 'AI_API_KEY is set'
    print(RULE)
    print('  OLIVER  |  AI Coding Harness  |  evaluation mode')
    print(RULE)
    print('  Model        ' + model_line)
    print('  Credential   ' + key_line)
    print('  Decoding     temperature {0}, seed {1}'.format(settings['temperature'], settings['seed']))
    print('  Limits       {0} model turns, {1:,} tokens, {2}s per task'.format(
        settings['max_steps'], settings['max_total_tokens'], settings['max_wall_seconds']))
    print('  Evidence     ' + config.display_path(config.RUNS_DIR) + '/')
    print(RULE, flush=True)


def preflight(settings):
    """One tiny model request before any task, so a bad key fails in seconds."""
    if foundation_model.is_mock_model(settings['model']):
        return True
    print('Checking the model... ', end='', flush=True)
    kind, message = foundation_model.check_model(settings)
    if not kind:
        print('ok')
        return True
    if kind == 'fatal':
        print('rejected')
        print('  ' + message)
        print('  The provider rejected the credential or the model. Check AI_API_KEY and the model setting.')
        return False
    print('no answer yet')
    print('  ' + message)
    print('  Continuing: tasks retry transient errors on their own.')
    return True


def ask(reader, prompt):
    """One line of input, or None when the input has ended."""
    try:
        return reader(prompt)
    except EOFError:
        return None


# --- collecting a task -------------------------------------------------------

def collect_task(reader, presets, configured_test_command):
    """Ask for repository, issue and test command. None when the input ends first."""
    repo_path, suggested_issue = '', ''
    answer = presets['repo']
    while not repo_path:
        if not answer:
            print('\nRepository to fix')
            print('  Enter the path of a local checkout, or a git URL to clone (https://github.com/owner/repo).')
            print('  Add a branch, tag or commit after a space to check it out.')
            answer = ask(reader, 'repository> ')
            if answer is None:
                return None
            answer = answer.strip()
            if not answer:
                continue
        if GITHUB_ISSUE.match(answer.split()[0]):
            suggested_issue = answer.split()[0]
        try:
            repo_path = prepare_repository(answer)
        except ValueError as error:
            print('  ' + str(error))
            answer = ''

    issue = ''
    if presets['issue']:
        try:
            issue = resolve_issue(presets['issue'])
        except ValueError as error:
            print('  ' + str(error))
    while not issue:
        print('\nIssue or test case')
        print('  Paste the issue text, then a line containing only ' + END_MARKER + ' (or press Ctrl-D).')
        print('  Or enter the path of a text file, or a GitHub issue URL.')
        if suggested_issue:
            print('  Press Enter to use ' + suggested_issue)
        first = ask(reader, 'issue> ')
        if first is None:
            return None
        if not first.strip() and suggested_issue:
            first = suggested_issue
        if not first.strip():
            continue
        try:
            issue = read_issue(reader, first)
        except ValueError as error:
            print('  ' + str(error))

    test_command = ''
    if not configured_test_command:
        print('\nTest command (optional)')
        print('  Press Enter to detect it, or type the command that runs the tests.')
        answer = ask(reader, 'tests> ')
        test_command = answer.strip() if answer else ''
    return {'repo': repo_path, 'issue': issue, 'test_command': test_command}


def read_issue(reader, first_line):
    """A GitHub issue URL or a file path on the first line, or pasted text up to END."""
    stripped = first_line.strip()
    if GITHUB_ISSUE.match(stripped) or os.path.isfile(os.path.expanduser(stripped)):
        return resolve_issue(stripped)
    lines = [] if stripped == END_MARKER else [first_line.rstrip()]
    while lines:
        line = ask(reader, '')
        if line is None or line.strip() == END_MARKER:
            break
        lines.append(line.rstrip())
    text = '\n'.join(lines).strip()
    if not text:
        raise ValueError('The issue text is empty.')
    return text


def resolve_issue(value):
    """The issue text from a GitHub issue URL, from a file, or the given text as is."""
    stripped = value.strip()
    if GITHUB_ISSUE.match(stripped):
        return fetch_github_issue(stripped)
    path = os.path.expanduser(stripped)
    if os.path.isfile(path):
        with open(path, 'r', encoding='utf-8', errors='replace') as handle:
            text = handle.read().strip()
        if not text:
            raise ValueError('The issue file is empty: ' + path)
        return text
    if not stripped:
        raise ValueError('The issue text is empty.')
    return stripped


def fetch_github_issue(url):
    """Title and body of a public GitHub issue or pull request."""
    owner, name, number = GITHUB_ISSUE.match(url).groups()
    api = 'https://api.github.com/repos/{0}/{1}/issues/{2}'.format(owner, name, number)
    request = urllib.request.Request(api, headers={'Accept': 'application/vnd.github+json',
                                                   'User-Agent': 'OLIVER-harness'})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            data = json.loads(response.read().decode('utf-8'))
    except (OSError, ValueError) as error:
        raise ValueError('Could not fetch {0} ({1}). Paste the issue text instead.'.format(url, error))
    title = data['title'] if 'title' in data and data['title'] else ''
    body = data['body'] if 'body' in data and data['body'] else ''
    return '# {0}\n\n{1}\n\n(GitHub issue: {2})'.format(title, body.strip(), url).strip()


# --- the repository ----------------------------------------------------------

def prepare_repository(answer):
    """Resolve the answer to a local directory, cloning URLs into workspace/.
    Returns an absolute path or raises ValueError with a message for the user."""
    parts = answer.split()
    location = parts[0]
    ref = parts[1] if len(parts) > 1 else ''
    issue = GITHUB_ISSUE.match(location)
    if issue:
        location = 'https://github.com/{0}/{1}.git'.format(issue.group(1), issue.group(2))
    if GIT_URL.match(location):
        return clone_repository(location, ref)
    if ref:
        raise ValueError('A branch, tag or commit can only be given together with a git URL.')
    path = os.path.realpath(os.path.expanduser(location))
    if not os.path.isdir(path):
        raise ValueError('Repository not found: ' + path)
    if overlaps_harness(path):
        raise ValueError('That directory contains the OLIVER harness. Enter the repository to fix.')
    return path


def overlaps_harness(path):
    root = os.path.realpath(config.PROJECT_ROOT)
    if is_within(path, os.path.realpath(config.WORKSPACE_DIR)) and path != os.path.realpath(config.WORKSPACE_DIR):
        return False
    return is_within(path, root) or is_within(root, path)


def is_within(path, parent):
    return path == parent or path.startswith(parent.rstrip(os.sep) + os.sep)


def clone_repository(url, ref):
    name = url.rstrip('/').split('/')[-1].split(':')[-1]
    name = re.sub(r'\.git$', '', name)
    name = re.sub(r'[^A-Za-z0-9_.-]', '-', name).strip('.-') or 'repository'
    base = os.path.join(config.WORKSPACE_DIR, name + '-' + datetime.datetime.now().strftime('%Y%m%d-%H%M%S'))
    target = base
    suffix = 2
    while os.path.exists(target):
        target = base + '-' + str(suffix)
        suffix += 1
    os.makedirs(config.WORKSPACE_DIR, exist_ok=True)
    print('  Cloning ' + url + (' at ' + ref if ref else '') + ' ...', flush=True)
    try:
        run_git(['clone', '--quiet'] + ([] if ref else ['--depth', '1']) + [url, target])
        if ref:
            run_git(['-C', target, 'checkout', '--quiet', ref])
    except ValueError as error:
        shutil.rmtree(target, ignore_errors=True)
        if 'could not read Username' in str(error):
            raise ValueError('Could not clone ' + url + ': the repository does not exist or is private '
                             '(private repositories need git credentials on this machine).')
        raise
    print('  Cloned into ' + config.display_path(target))
    return target


def run_git(arguments):
    """Run git without ever prompting for credentials; failures raise ValueError.
    git keeps the user's own credentials (ssh agent, helpers) but never sees AI_API_KEY."""
    env = dict(os.environ)
    if foundation_model.API_KEY_VARIABLE in env:
        del env[foundation_model.API_KEY_VARIABLE]
    env['GIT_TERMINAL_PROMPT'] = '0'
    try:
        completed = subprocess.run(['git'] + arguments, capture_output=True, text=True, timeout=900, env=env,
                                   stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise ValueError('git could not run: ' + str(error))
    if completed.returncode != 0:
        detail = (completed.stderr.strip() or completed.stdout.strip())[-600:]
        raise ValueError('git failed: ' + detail)


# --- running and reporting ---------------------------------------------------

def run_task(task, settings, out_dir):
    task_settings = dict(settings)
    if task['test_command']:
        task_settings['test_command'] = task['test_command']
    first_line = task['issue'].strip().splitlines()[0][:100]
    print('\n' + RULE)
    print('OLIVER is working on: ' + first_line)
    print('Repository: ' + task['repo'])
    print('Tests: ' + (task_settings['test_command'] or 'detected automatically'))
    print(RULE, flush=True)
    result = orchestrator.run_agent_loop(task['issue'], task['repo'], task_settings)
    run_dir = report.write_run_artifacts(result, out_dir)
    print_result(result, run_dir)
    return result


def print_result(result, run_dir):
    usage = result['usage']
    status = result['status']
    lines = [RULE, 'Result: ' + (VERDICTS[status] if status in VERDICTS else status.upper())]
    if result['verification']:
        lines += ['  ' + line for line in describe_verification(result['verification']).splitlines()]
    for key, label in (('stop_reason', 'Stopped'), ('fatal_error', 'Error'), ('note', 'Note')):
        if result[key]:
            lines.append(label + ': ' + result[key])
    changed = ', '.join(item['path'] + ' (' + item['status'] + ')' for item in result['changed_files'])
    lines.append('Changed files: ' + (changed if changed else 'none'))
    lines.append('Usage: {0} model turns, {1:,} tokens, ${2:.4f}, {3}s'.format(
        result['steps'], usage['total_tokens'], usage['cost'], result['duration_seconds']))
    lines.append('Repository: ' + result['repo'] + (' (patch applied)' if result['patch'].strip() else ''))
    lines.append('Evidence: ' + config.display_path(run_dir) + '/  (' + ', '.join(report.EVIDENCE_FILES) + ')')
    if result['patch'].strip():
        patch_lines = result['patch'].rstrip('\n').splitlines()
        lines.append('--- patch.diff ' + '-' * 57)
        lines += patch_lines[:PATCH_PREVIEW_LINES]
        if len(patch_lines) > PATCH_PREVIEW_LINES:
            lines.append('... {0} more lines in patch.diff'.format(len(patch_lines) - PATCH_PREVIEW_LINES))
    lines.append(RULE)
    print(foundation_model.redact_secret('\n'.join(lines)), flush=True)
