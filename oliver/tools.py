"""Tools the OLIVER model can call.

Design rules (the agent-computer interface):
- Every tool returns a short, readable string; errors start with "ERROR:" and
  say how to fix the call.
- Paths are confined to the repository root (symlinks resolved, .git blocked).
- Edits are exact-match replacements; every edit to a Python or JSON file is
  syntax-checked and rolled back automatically if it breaks the file.
- Arguments are type-checked with pydantic.validate_call, applied as a plain
  function call (no classes, no decorator syntax).

The three required tools keep their exact signatures:
read_file(filepath), write_file(filepath, content), search_repo(query).
"""

import fnmatch
import json
import os
import re
import shutil
import subprocess
from difflib import get_close_matches

from pydantic import ValidationError, validate_call

from oliver import context_manager
from oliver import verification

TOOL_CONTEXT = {
    'repo_root': '',
    'settings': {},
    'index': None,
    'test_command': '',
    'finished': False,
    'finish_summary': '',
}

PLAN_TOOLS = ['read_file', 'search_repo', 'list_files']
ALL_TOOLS = ['read_file', 'search_repo', 'list_files', 'edit_file', 'write_file', 'run_command', 'run_tests',
             'finish']

MAX_WRITE_BYTES = 1000000
MAX_SEARCH_MATCHES = 60
MAX_SEARCH_FILES = 25
MAX_LIST_ENTRIES = 200

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


# --- context -----------------------------------------------------------------

def set_tool_context(repo_root, settings, index, test_command):
    TOOL_CONTEXT['repo_root'] = os.path.realpath(repo_root)
    TOOL_CONTEXT['settings'] = settings
    TOOL_CONTEXT['index'] = index
    TOOL_CONTEXT['test_command'] = test_command
    TOOL_CONTEXT['finished'] = False
    TOOL_CONTEXT['finish_summary'] = ''


def reset_finish():
    TOOL_CONTEXT['finished'] = False


def resolve_repo_path(filepath):
    """Return (absolute_path, error). Refuses anything outside the repository."""
    root = TOOL_CONTEXT['repo_root']
    if not root:
        return '', 'ERROR: tool context is not initialised'
    if not isinstance(filepath, str) or not filepath.strip():
        return '', 'ERROR: filepath must be a non-empty relative path'
    cleaned = filepath.strip()
    candidate = cleaned if os.path.isabs(cleaned) else os.path.join(root, cleaned)
    resolved = os.path.realpath(candidate)
    if resolved != root and not resolved.startswith(root + os.sep):
        return '', 'ERROR: path "' + cleaned + '" is outside the repository'
    relative = os.path.relpath(resolved, root)
    if relative.split(os.sep)[0] == '.git' or '/.git/' in '/' + relative.replace(os.sep, '/') + '/':
        return '', 'ERROR: the .git directory is managed by the harness'
    return resolved, ''


def relative_path(absolute):
    return os.path.relpath(absolute, TOOL_CONTEXT['repo_root']).replace(os.sep, '/')


def read_text_file(path):
    with open(path, 'rb') as handle:
        data = handle.read()
    if b'\x00' in data[:8192]:
        return None
    return data.decode('utf-8', 'replace')


def number_lines(lines, first_number):
    width = len(str(first_number + len(lines)))
    return '\n'.join(str(first_number + offset).rjust(width) + '\t' + line[:1000]
                     for offset, line in enumerate(lines))


# --- file tools --------------------------------------------------------------

def read_file(filepath: str, start_line: int = 1, end_line: int = 0) -> str:
    """Read a text file with line numbers. end_line 0 means 'to the window limit'."""
    path, error = resolve_repo_path(filepath)
    if error:
        return error
    if not os.path.isfile(path):
        return 'ERROR: file not found: ' + filepath + '. Use list_files or search_repo to find the right path.'
    text = read_text_file(path)
    if text is None:
        return 'ERROR: ' + filepath + ' is a binary file'
    lines = text.splitlines()
    window = TOOL_CONTEXT['settings']['read_window']
    total = len(lines)
    start = max(1, start_line)
    if total == 0:
        return '(empty file: ' + relative_path(path) + ')'
    if start > total:
        return 'ERROR: start_line {0} is past the end of the file ({1} lines)'.format(start, total)
    end = total if end_line <= 0 else min(end_line, total)
    if end < start:
        return 'ERROR: end_line must be >= start_line'
    truncated = False
    if end - start + 1 > window:
        end = start + window - 1
        truncated = True
    header = '{0} (lines {1}-{2} of {3})'.format(relative_path(path), start, end, total)
    body = number_lines(lines[start - 1:end], start)
    if truncated:
        body += '\n[{0} more lines; call read_file with start_line={1} to continue]'.format(total - end, end + 1)
    return header + '\n' + body


def guarded_write(path, new_text, previous_text):
    """Write, then syntax-check; restore previous content if the file breaks."""
    with open(path, 'w', encoding='utf-8') as handle:
        handle.write(new_text)
    problem = verification.check_syntax(path)
    if not problem:
        return ''
    if previous_text is None:
        os.remove(path)
    else:
        with open(path, 'w', encoding='utf-8') as handle:
            handle.write(previous_text)
    return problem


def write_file(filepath: str, content: str) -> str:
    """Create a new file (or fully replace a small one). Prefer edit_file for changes."""
    path, error = resolve_repo_path(filepath)
    if error:
        return error
    if os.path.isdir(path):
        return 'ERROR: ' + filepath + ' is a directory'
    if len(content.encode('utf-8')) > MAX_WRITE_BYTES:
        return 'ERROR: content is larger than 1 MB'
    previous = None
    if os.path.isfile(path):
        previous = read_text_file(path)
        if previous is None:
            return 'ERROR: refusing to overwrite a binary file'
    parent = os.path.dirname(path)
    if not os.path.isdir(parent):
        os.makedirs(parent, exist_ok=True)
    problem = guarded_write(path, content, previous)
    if problem:
        return ('ERROR: the new content does not parse ({0}). The file was left unchanged. '
                'Fix the syntax and call write_file again.').format(problem)
    verb = 'Replaced' if previous is not None else 'Created'
    return '{0} {1} ({2} lines).'.format(verb, relative_path(path), len(content.splitlines()))


def edit_file(filepath: str, old_text: str, new_text: str) -> str:
    """Replace exactly one occurrence of old_text with new_text."""
    path, error = resolve_repo_path(filepath)
    if error:
        return error
    if not os.path.isfile(path):
        return 'ERROR: file not found: ' + filepath + '. Use write_file to create new files.'
    if not old_text:
        return 'ERROR: old_text must not be empty'
    if old_text == new_text:
        return 'ERROR: old_text and new_text are identical; nothing to change'
    original = read_text_file(path)
    if original is None:
        return 'ERROR: ' + filepath + ' is a binary file'
    count = original.count(old_text)
    if count == 0:
        return 'ERROR: old_text was not found in ' + relative_path(path) + '.' + closest_region(original, old_text)
    if count > 1:
        return ('ERROR: old_text matches {0} places in {1}. Include more surrounding lines so it '
                'matches exactly once.').format(count, relative_path(path))
    updated = original.replace(old_text, new_text, 1)
    problem = guarded_write(path, updated, original)
    if problem:
        return ('ERROR: this edit would break the file ({0}). The edit was rolled back; the file is '
                'unchanged. Fix the new_text and try again.').format(problem)
    start_line = original[:original.index(old_text)].count('\n') + 1
    new_lines = updated.splitlines()
    first = max(1, start_line - 3)
    last = min(len(new_lines), start_line + len(new_text.splitlines()) + 3)
    return 'Edited {0}. Lines {1}-{2} now read:\n{3}'.format(
        relative_path(path), first, last, number_lines(new_lines[first - 1:last], first))


def closest_region(text, old_text):
    """Point the model at the most similar lines when an exact match fails."""
    lines = text.splitlines()
    wanted = [line.strip() for line in old_text.splitlines() if line.strip()]
    if not wanted:
        return ''
    stripped = [line.strip() for line in lines]
    candidates = get_close_matches(wanted[0], stripped, n=1, cutoff=0.6)
    if not candidates:
        return ' Re-read the file and copy old_text exactly, including indentation.'
    position = stripped.index(candidates[0])
    first = max(0, position - 2)
    last = min(len(lines), position + len(wanted) + 2)
    return (' The closest match is at line {0}; copy it exactly (whitespace matters):\n{1}').format(
        position + 1, number_lines(lines[first:last], first + 1))


# --- search tools ------------------------------------------------------------

def search_repo(query: str, path_glob: str = '', regex: bool = False) -> str:
    """Search file contents (ripgrep) and symbol definitions (repository index)."""
    query = query.strip()
    if not query:
        return 'ERROR: query must not be empty'
    sections = []
    definitions = find_definitions(query)
    if definitions:
        sections.append('Symbol definitions:\n' + '\n'.join(definitions))
    matches, error = text_matches(query, path_glob, regex)
    if error:
        return error
    if not matches:
        sections.append('No text matches for "' + query + '".')
        return '\n\n'.join(sections)
    by_file = {}
    order = []
    for path, number, line in matches:
        if path not in by_file:
            by_file[path] = []
            order.append(path)
        by_file[path].append((number, line))
    shown = 0
    lines = ['Text matches ({0} in {1} files):'.format(len(matches), len(order))]
    for path in order[:MAX_SEARCH_FILES]:
        lines.append(path)
        for number, line in by_file[path]:
            if shown >= MAX_SEARCH_MATCHES:
                break
            lines.append('  {0}: {1}'.format(number, line.strip()[:200]))
            shown += 1
    if len(matches) > shown or len(order) > MAX_SEARCH_FILES:
        lines.append('[results truncated; narrow the query or pass path_glob]')
    sections.append('\n'.join(lines))
    return '\n\n'.join(sections)


def find_definitions(query):
    index = TOOL_CONTEXT['index']
    if not index:
        return []
    needle = query.split('(')[0].strip().split('.')[-1]
    found = []
    for rel in sorted(index['symbols']):
        for symbol in index['symbols'][rel]:
            short = symbol['name'].split('.')[-1]
            if short == needle:
                found.append('  {0}:{1}  {2} {3}'.format(rel, symbol['line'], symbol['kind'], symbol['name']))
    return found[:20]


def text_matches(query, path_glob, regex):
    root = TOOL_CONTEXT['repo_root']
    if shutil.which('rg'):
        command = ['rg', '--line-number', '--no-heading', '--color', 'never', '--max-columns', '300',
                   '--max-count', '20', '--smart-case']
        if not regex:
            command.append('--fixed-strings')
        if path_glob:
            command += ['--glob', path_glob]
        command += ['--', query, '.']
        try:
            completed = subprocess.run(command, cwd=root, capture_output=True, timeout=30)
        except (OSError, subprocess.SubprocessError) as error:
            return [], 'ERROR: search failed: ' + str(error)
        if completed.returncode == 2:
            return [], 'ERROR: invalid search: ' + completed.stderr.decode('utf-8', 'replace')[:300]
        results = []
        for raw in completed.stdout.decode('utf-8', 'replace').splitlines():
            parts = raw.split(':', 2)
            if len(parts) == 3 and parts[1].isdigit():
                path = parts[0][2:] if parts[0].startswith('./') else parts[0]
                if not context_manager.is_skipped_path(path):
                    results.append((path, int(parts[1]), parts[2]))
        return results, ''
    return python_text_matches(query, path_glob, regex)


def python_text_matches(query, path_glob, regex):
    index = TOOL_CONTEXT['index']
    root = TOOL_CONTEXT['repo_root']
    try:
        pattern = re.compile(query if regex else re.escape(query), 0 if query != query.lower() else re.IGNORECASE)
    except re.error as error:
        return [], 'ERROR: invalid regex: ' + str(error)
    results = []
    for rel in index['files'] if index else []:
        if path_glob and not fnmatch.fnmatch(rel, path_glob):
            continue
        try:
            text = read_text_file(os.path.join(root, rel))
        except OSError:
            continue
        if text is None:
            continue
        for number, line in enumerate(text.splitlines(), start=1):
            if pattern.search(line):
                results.append((rel, number, line))
                if len(results) >= 500:
                    return results, ''
    return results, ''


def list_files(path: str = '.', pattern: str = '') -> str:
    """List repository files under a directory, optionally filtered by a glob."""
    target, error = resolve_repo_path(path if path else '.')
    if error:
        return error
    prefix = relative_path(target)
    prefix = '' if prefix == '.' else prefix.rstrip('/') + '/'
    index = TOOL_CONTEXT['index']
    files = index['files'] if index else context_manager.list_repo_files(TOOL_CONTEXT['repo_root'])
    selected = []
    for rel in files:
        if prefix and not rel.startswith(prefix):
            continue
        if pattern and not (fnmatch.fnmatch(rel, pattern) or fnmatch.fnmatch(os.path.basename(rel), pattern)):
            continue
        selected.append(rel)
    if not selected:
        return 'No files found under "' + (prefix or '.') + '"' + (' matching ' + pattern if pattern else '') + '.'
    lines = ['{0} files under {1}:'.format(len(selected), prefix or './')]
    lines += selected[:MAX_LIST_ENTRIES]
    if len(selected) > MAX_LIST_ENTRIES:
        lines.append('[{0} more; pass a narrower path or pattern]'.format(len(selected) - MAX_LIST_ENTRIES))
    return '\n'.join(lines)


# --- execution tools ---------------------------------------------------------

def blocked_reason(command):
    for pattern, reason in BLOCKED_COMMANDS:
        if pattern.search(command):
            return reason
    return ''


def run_command(command: str, timeout_seconds: int = 120) -> str:
    """Run a shell command in the repository root (sandboxed, secrets removed)."""
    if not command.strip():
        return 'ERROR: command must not be empty'
    reason = blocked_reason(command)
    if reason:
        return 'ERROR: command refused: ' + reason + '.'
    settings = TOOL_CONTEXT['settings']
    limit = max(1, min(timeout_seconds, settings['command_timeout']))
    result = verification.execute_command(command, TOOL_CONTEXT['repo_root'], limit, settings)
    output = (result['stdout'] + ('\n' if result['stdout'] and result['stderr'] else '') + result['stderr']).strip()
    status = 'exit code {0}'.format(result['exit_code'])
    if result['timed_out']:
        status += ' (timed out after {0}s)'.format(limit)
    return status + '\n' + context_manager.truncate_output(output if output else '(no output)',
                                                          settings['max_tool_output_chars'])


def run_tests(target: str = '') -> str:
    """Run the repository's tests (optionally one file or test id) and summarise."""
    test_command = TOOL_CONTEXT['test_command']
    if not test_command:
        return 'ERROR: no test command was detected. Use run_command to run tests directly.'
    targets = []
    if target.strip():
        path_part = target.split('::')[0]
        resolved, error = resolve_repo_path(path_part)
        if error:
            return error
        targets = [relative_path(resolved) + target[len(path_part):]]
    results = verification.run_tests(TOOL_CONTEXT['repo_root'], test_command, targets, TOOL_CONTEXT['settings'])
    lines = ['{0}\nexit code {1}{2}: {3}'.format(
        results['command'], results['exit_code'], ' (timed out)' if results['timed_out'] else '',
        verification.summarize_tests(results))]
    failing = [test for test in sorted(results['outcomes']) if results['outcomes'][test] in verification.FAILING]
    for test in failing[:10]:
        detail = results['details'][test] if test in results['details'] else ''
        lines.append('--- ' + test + '\n' + context_manager.truncate_output(detail, 1500))
    if not results['collected'] or not failing and results['exit_code'] not in (0, 5):
        lines.append(results['output'])
    return context_manager.truncate_output('\n'.join(lines), TOOL_CONTEXT['settings']['max_tool_output_chars'])


def finish(summary: str = '') -> str:
    """Signal that the change is complete. The harness then verifies it."""
    TOOL_CONTEXT['finished'] = True
    TOOL_CONTEXT['finish_summary'] = summary.strip()
    return 'Finish recorded. The harness will now verify the change.'


# --- specs and dispatch ------------------------------------------------------

def spec(name, description, properties, required):
    return {
        'type': 'function',
        'function': {
            'name': name,
            'description': description,
            'parameters': {'type': 'object', 'properties': properties, 'required': required},
        },
    }


TOOL_SPECS = [
    spec('read_file', 'Read a text file with line numbers. Use start_line/end_line for large files.',
         {'filepath': {'type': 'string', 'description': 'Path relative to the repository root.'},
          'start_line': {'type': 'integer', 'description': 'First line to show (1-based).'},
          'end_line': {'type': 'integer', 'description': 'Last line to show; 0 means to the window limit.'}},
         ['filepath']),
    spec('search_repo', 'Search the repository: symbol definitions plus text matches (ripgrep).',
         {'query': {'type': 'string', 'description': 'Text to find (literal unless regex is true).'},
          'path_glob': {'type': 'string', 'description': 'Optional glob such as "src/**/*.py".'},
          'regex': {'type': 'boolean', 'description': 'Treat query as a regular expression.'}},
         ['query']),
    spec('list_files', 'List repository files under a directory.',
         {'path': {'type': 'string', 'description': 'Directory relative to the root (default ".").'},
          'pattern': {'type': 'string', 'description': 'Optional glob filter such as "*.py".'}},
         []),
    spec('edit_file', 'Replace exactly one occurrence of old_text with new_text in an existing file. '
                      'Copy old_text exactly from read_file output (without line numbers).',
         {'filepath': {'type': 'string', 'description': 'Path relative to the repository root.'},
          'old_text': {'type': 'string', 'description': 'Exact text to replace; must match once.'},
          'new_text': {'type': 'string', 'description': 'Replacement text.'}},
         ['filepath', 'old_text', 'new_text']),
    spec('write_file', 'Create a new file with the given content (or fully replace a small file).',
         {'filepath': {'type': 'string', 'description': 'Path relative to the repository root.'},
          'content': {'type': 'string', 'description': 'Complete file content.'}},
         ['filepath', 'content']),
    spec('run_command', 'Run a shell command in the repository root, e.g. a reproduction script.',
         {'command': {'type': 'string', 'description': 'Shell command.'},
          'timeout_seconds': {'type': 'integer', 'description': 'Timeout in seconds (capped by the harness).'}},
         ['command']),
    spec('run_tests', 'Run the test suite, or one test file / test id, and summarise failures.',
         {'target': {'type': 'string', 'description': 'Optional test file or id, e.g. tests/test_x.py::test_y.'}},
         []),
    spec('finish', 'Call when the change is complete and you have verified it.',
         {'summary': {'type': 'string', 'description': 'Root cause and what was changed.'}},
         ['summary']),
]

TOOL_FUNCTIONS = {
    'read_file': validate_call(read_file),
    'write_file': validate_call(write_file),
    'edit_file': validate_call(edit_file),
    'search_repo': validate_call(search_repo),
    'list_files': validate_call(list_files),
    'run_command': validate_call(run_command),
    'run_tests': validate_call(run_tests),
    'finish': validate_call(finish),
}


def tool_specs_for(names):
    return [item for item in TOOL_SPECS if item['function']['name'] in names]


def format_validation_error(name, error):
    problems = []
    for item in error.errors():
        location = '.'.join(str(part) for part in item['loc']) if item['loc'] else 'arguments'
        problems.append(location + ': ' + item['msg'])
    return 'ERROR: invalid arguments for ' + name + ': ' + '; '.join(problems)


def dispatch_tool_call(name, arguments):
    """Run one tool call. Returns {'ok': bool, 'output': str}; never raises."""
    if name not in TOOL_FUNCTIONS:
        return {'ok': False, 'output': 'ERROR: unknown tool "' + str(name) + '". Available: ' + ', '.join(ALL_TOOLS)}
    if not isinstance(arguments, dict):
        return {'ok': False, 'output': 'ERROR: arguments for ' + name + ' must be a JSON object'}
    try:
        output = TOOL_FUNCTIONS[name](**arguments)
    except ValidationError as error:
        return {'ok': False, 'output': format_validation_error(name, error)}
    except Exception as error:
        return {'ok': False, 'output': 'ERROR: ' + name + ' failed: ' + type(error).__name__ + ': ' + str(error)}
    return {'ok': not output.startswith('ERROR:'), 'output': output}


def call_label(name, arguments):
    """A short label for trajectories and reports."""
    if not isinstance(arguments, dict) or not arguments:
        return ''
    for key in ('filepath', 'query', 'command', 'target', 'path', 'summary'):
        if key in arguments:
            return str(arguments[key])[:120]
    return json.dumps(arguments)[:120]
