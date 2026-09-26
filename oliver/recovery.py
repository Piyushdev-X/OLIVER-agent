"""Recovery for OLIVER: classify failures, build recovery prompts, roll back.

Checkpoints live in a "shadow" git repository kept outside the target repo
(git --git-dir=<temp> --work-tree=<repo>). That gives exact baselines,
checkpoints, rollback and the final patch without ever touching the target
repository's own git history.
"""

import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile

SHADOW_EXCLUDES = ['.oliver-*', '__pycache__/', '*.pyc', '.pytest_cache/', '.mypy_cache/', '.ruff_cache/',
                   '.coverage', 'htmlcov/', '*.egg-info/', '.tox/', 'node_modules/', '.venv/', 'venv/']
EXCEPTION_LINE = re.compile(r'^\s*(?:E\s+)?([A-Za-z_][\w.]*(?:Error|Exception|Exit|Interrupt))\b:?\s*(.*)$')


# --- failure classification --------------------------------------------------

def failing_check(verification_result, name):
    for check in verification_result['checks']:
        if check['name'] == name and not check['ok']:
            return check
    return None


def classify_failure(verification_result):
    """Turn a failed verification into {'kind', 'headline', 'details', 'signature'}."""
    changes = failing_check(verification_result, 'changes')
    syntax = failing_check(verification_result, 'syntax')
    reproduction = failing_check(verification_result, 'reproduction')
    tests = failing_check(verification_result, 'tests')
    comparison = verification_result['comparison']
    test_results = verification_result['tests']
    if changes:
        kind, headline, details = 'no_changes', 'no files were changed', changes['detail']
    elif syntax:
        kind, headline, details = 'syntax_error', 'a changed file does not parse', syntax['detail']
    elif tests and test_results and test_results['timed_out']:
        kind, headline, details = 'timeout', 'the test run timed out', tests['detail']
    elif tests and comparison and (comparison['broken'] or comparison['missing']):
        kind, headline = 'regression', 'the change broke tests that passed before'
        details = tests['detail'] + '\n' + failure_details(test_results, comparison['broken'])
    elif reproduction:
        kind, headline, details = 'reproduction_failed', 'the reproduction command still fails', reproduction['detail']
    elif tests:
        failing = []
        if comparison:
            failing = comparison['new_failing'] + comparison['still_failing']
        kind = 'new_test_failure' if comparison and comparison['new_failing'] else 'test_failure'
        headline = 'tests still fail'
        details = tests['detail'] + '\n' + failure_details(test_results, failing)
    else:
        kind, headline, details = 'unknown', 'verification failed', json.dumps(verification_result['checks'])[:2000]
    text = trim_traceback(details, 60)
    if kind in ('regression', 'test_failure', 'new_test_failure', 'reproduction_failed') and looks_like_import_error(text):
        kind = 'import_error'
    return {'kind': kind, 'headline': headline, 'details': text, 'signature': error_signature(kind, text)}


def failure_details(test_results, tests):
    if not test_results:
        return ''
    parts = []
    for test in tests[:5]:
        if test in test_results['details']:
            parts.append('--- ' + test + '\n' + trim_traceback(test_results['details'][test], 25))
    if not parts:
        parts.append(test_results['output'])
    return '\n'.join(parts)


def looks_like_import_error(text):
    return 'ModuleNotFoundError' in text or 'ImportError' in text


def trim_traceback(text, max_lines):
    """Keep the lines that explain a failure: headers, assertion lines, the last
    frames and the final exception. Drop library frames."""
    lines = [line for line in text.splitlines() if 'site-packages' not in line and '/lib/python' not in line]
    if len(lines) <= max_lines:
        return '\n'.join(lines)
    important = []
    for position, line in enumerate(lines):
        stripped = line.strip()
        if (stripped.startswith('E ') or stripped.startswith('>') or stripped.startswith('File ')
                or stripped.startswith('FAIL') or stripped.startswith('___') or EXCEPTION_LINE.match(line)
                or 'assert' in stripped):
            important.append(position)
    keep = set(important[-max_lines:])
    keep.update(range(max(0, len(lines) - 5), len(lines)))
    trimmed = [lines[position] for position in sorted(keep)]
    return '\n'.join(trimmed[-max_lines:])


def error_signature(kind, text):
    """A stable fingerprint of a failure, used to spot repeated failures."""
    last_exception = ''
    for line in text.splitlines():
        match = EXCEPTION_LINE.match(line)
        if match:
            last_exception = match.group(1) + ': ' + match.group(2)
    basis = last_exception if last_exception else text[-400:]
    normalized = re.sub(r'0x[0-9a-fA-F]+', 'ADDR', basis)
    normalized = re.sub(r'\d+', 'N', normalized)
    normalized = re.sub(r'\s+', ' ', normalized).strip()
    digest = hashlib.sha1((kind + '|' + normalized).encode('utf-8')).hexdigest()[:10]
    return kind + ':' + digest + ' ' + normalized[:160]


def should_replan(signatures):
    """Replan when the same failure happens twice in a row."""
    return len(signatures) >= 2 and signatures[-1].split(' ')[0] == signatures[-2].split(' ')[0]


def action_signature(name, arguments):
    try:
        encoded = json.dumps(arguments, sort_keys=True)
    except (TypeError, ValueError):
        encoded = str(arguments)
    return name + ' ' + encoded


def detect_loop(action_history, threshold):
    """True when the last `threshold` actions are identical."""
    if len(action_history) < threshold:
        return False
    tail = action_history[-threshold:]
    return all(entry == tail[0] for entry in tail)


# --- shadow git snapshots ----------------------------------------------------

def git_environment():
    env = dict(os.environ)
    env['GIT_AUTHOR_NAME'] = 'OLIVER'
    env['GIT_AUTHOR_EMAIL'] = 'oliver@localhost'
    env['GIT_COMMITTER_NAME'] = 'OLIVER'
    env['GIT_COMMITTER_EMAIL'] = 'oliver@localhost'
    env['GIT_TERMINAL_PROMPT'] = '0'
    return env


def git_command(snapshot, arguments):
    command = ['git', '--git-dir=' + snapshot['git_dir'], '--work-tree=' + snapshot['work_tree']] + arguments
    return subprocess.run(command, cwd=snapshot['work_tree'], capture_output=True, timeout=300,
                          env=git_environment())


def git_output(snapshot, arguments):
    completed = git_command(snapshot, arguments)
    if completed.returncode != 0:
        raise RuntimeError('git ' + ' '.join(arguments) + ' failed: '
                           + completed.stderr.decode('utf-8', 'replace').strip()[:500])
    return completed.stdout.decode('utf-8', 'replace')


def init_snapshots(repo_root):
    git_dir = tempfile.mkdtemp(prefix='oliver-shadow-')
    snapshot = {'git_dir': git_dir, 'work_tree': os.path.realpath(repo_root), 'baseline': '', 'checkpoints': []}
    git_output(snapshot, ['init', '-q'])
    git_output(snapshot, ['config', 'core.autocrlf', 'false'])
    git_output(snapshot, ['config', 'core.quotepath', 'false'])
    exclude_dir = os.path.join(git_dir, 'info')
    os.makedirs(exclude_dir, exist_ok=True)
    with open(os.path.join(exclude_dir, 'exclude'), 'a', encoding='utf-8') as handle:
        handle.write('\n'.join(SHADOW_EXCLUDES) + '\n')
    force_track_target_files(snapshot)
    snapshot['baseline'] = commit_all(snapshot, 'baseline')
    return snapshot


def force_track_target_files(snapshot):
    """The target's .gitignore files also apply to the shadow repo. Force-track every
    file tracked by the target repository, so edits to tracked-but-ignored files
    still show up in changed_files, the patch and rollbacks."""
    work_tree = snapshot['work_tree']
    if not os.path.isdir(os.path.join(work_tree, '.git')):
        return
    try:
        listed = subprocess.run(['git', 'ls-files', '-z'], cwd=work_tree, capture_output=True, timeout=120)
    except (OSError, subprocess.SubprocessError):
        return
    if listed.returncode != 0:
        return
    root = work_tree.encode('utf-8')
    paths = [raw for raw in listed.stdout.split(b'\x00') if raw and os.path.isfile(os.path.join(root, raw))]
    if not paths:
        return
    spec_file = os.path.join(snapshot['git_dir'], 'oliver-tracked-paths')
    with open(spec_file, 'wb') as handle:
        handle.write(b'\x00'.join(paths))
    git_output(snapshot, ['add', '-f', '--pathspec-from-file=' + spec_file, '--pathspec-file-nul'])


def commit_all(snapshot, message):
    git_output(snapshot, ['add', '-A'])
    git_output(snapshot, ['commit', '-q', '--allow-empty', '--no-verify', '-m', message])
    return git_output(snapshot, ['rev-parse', 'HEAD']).strip()


def create_checkpoint(snapshot, label):
    sha = commit_all(snapshot, label)
    snapshot['checkpoints'].append({'sha': sha, 'label': label})
    return sha


def restore_checkpoint(snapshot, sha):
    """Make the working tree match a checkpoint exactly (ignored files untouched)."""
    commit_all(snapshot, 'before-rollback')
    git_output(snapshot, ['reset', '-q', '--hard', sha])


def changed_files(snapshot):
    git_output(snapshot, ['add', '-A'])
    output = git_output(snapshot, ['diff', '--cached', '--name-status', '--no-renames', snapshot['baseline']])
    changes = []
    for line in output.splitlines():
        if '\t' in line:
            status, path = line.split('\t', 1)
            changes.append({'status': status[:1], 'path': path})
    return changes


def export_patch(snapshot):
    git_output(snapshot, ['add', '-A'])
    return git_output(snapshot, ['diff', '--cached', '--binary', '--no-renames', snapshot['baseline']])


def cleanup_snapshots(snapshot):
    if snapshot and snapshot['git_dir'] and os.path.isdir(snapshot['git_dir']):
        shutil.rmtree(snapshot['git_dir'], ignore_errors=True)
