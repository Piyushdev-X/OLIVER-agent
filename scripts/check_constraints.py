"""Constraint checker for the OLIVER codebase.

Every rule below is a hard hackathon constraint. The banned tokens are
assembled from fragments at runtime so this file passes its own scan.

  R0  Python files must parse
  R1  no class definitions (purely procedural code)
  R2  no decorators and no meta-programming builtins
  R3  no occurrence of the four-letter instance-receiver word, in any case,
      in any file (guards against naive greps for its dotted form)
  R4  no get-method calls: no dotted get-prefixed call and no 'get' + '('
  R5  no CSS var function, anywhere
  R6  no media-query at-rules, anywhere
  R7  no CSS custom property declarations in web files
  R8  no chronological track ('time' + 'line') elements outside Markdown
  R9  no retired project names anywhere (the product is branded OLIVER)

Usage:
  python3 scripts/check_constraints.py            scan the whole repository
  python3 scripts/check_constraints.py FILE ...   scan specific files
  python3 scripts/check_constraints.py --hook     Claude Code PostToolUse mode
"""

import ast
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

RECEIVER_WORD = 's' + 'elf'
GET_CALL = 'g' + 'et('
DOTTED_GET = re.compile(r'\.' + 'g' + r'et[A-Za-z0-9_]*\(', re.IGNORECASE)
VAR_CALL = 'v' + 'ar('
MEDIA_RULE = '@' + 'media'
TRACK_WORD = 'time' + 'line'
CUSTOM_PROPERTY = re.compile(r'(^|[\s;{])--[A-Za-z0-9_-]+\s*:')
RETIRED_NAMES = ['arth' + 'kram']
META_BUILTINS = ['exec', 'eval', 'setattr', 'getattr', 'delattr',
                 '__import__', 'globals', 'locals', 'vars']

WEB_EXTENSIONS = ['.html', '.htm', '.css', '.svg', '.js']
SKIP_DIRS = ['.git', '__pycache__', '.venv', 'venv', 'node_modules', 'runs',
             '.pytest_cache', 'build', 'dist', '.mypy_cache', '.ruff_cache']


def make_violation(path, line, rule, message):
    return {'path': path, 'line': line, 'rule': rule, 'message': message}


def list_repo_files(root):
    found = []
    for current, dirs, files in os.walk(root):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS)
        for name in sorted(files):
            found.append(os.path.join(current, name))
    return found


def read_text(path):
    try:
        with open(path, 'rb') as handle:
            data = handle.read()
    except OSError:
        return None
    if b'\x00' in data:
        return None
    try:
        return data.decode('utf-8')
    except UnicodeDecodeError:
        return None


def scan_lines(rel_path, text):
    violations = []
    extension = os.path.splitext(rel_path)[1].lower()
    is_web = extension in WEB_EXTENSIONS
    is_markdown = extension == '.md'
    for number, line in enumerate(text.splitlines(), start=1):
        lowered = line.lower()
        if RECEIVER_WORD in lowered:
            violations.append(make_violation(
                rel_path, number, 'R3',
                'instance-receiver word found; the codebase is procedural'))
        if GET_CALL in lowered or DOTTED_GET.search(line):
            violations.append(make_violation(
                rel_path, number, 'R4',
                'get-method style call found; use key-in checks and brackets'))
        if VAR_CALL in lowered:
            violations.append(make_violation(
                rel_path, number, 'R5',
                'CSS var function found; hardcode the value'))
        if MEDIA_RULE in lowered:
            violations.append(make_violation(
                rel_path, number, 'R6',
                'media-query at-rule found; layout is fixed-width'))
        if is_web and CUSTOM_PROPERTY.search(line):
            violations.append(make_violation(
                rel_path, number, 'R7',
                'CSS custom property declaration found; hardcode the value'))
        if not is_markdown and TRACK_WORD in lowered:
            violations.append(make_violation(
                rel_path, number, 'R8',
                TRACK_WORD + ' element found; these are banned in the UI'))
        for name in RETIRED_NAMES:
            if name in lowered:
                violations.append(make_violation(
                    rel_path, number, 'R9',
                    'retired project name found; use OLIVER'))
    return violations


def scan_python(rel_path, text):
    try:
        tree = ast.parse(text, filename=rel_path)
    except SyntaxError as error:
        return [make_violation(rel_path, error.lineno or 0, 'R0',
                               'syntax error: ' + str(error.msg))]
    violations = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            violations.append(make_violation(
                rel_path, node.lineno, 'R1',
                'class definition "' + node.name + '"; write functions instead'))
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.decorator_list:
                violations.append(make_violation(
                    rel_path, node.lineno, 'R2',
                    'decorator on "' + node.name + '"; call wrappers explicitly'))
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            name = node.func.id
            if name in META_BUILTINS:
                violations.append(make_violation(
                    rel_path, node.lineno, 'R2',
                    'meta-programming builtin "' + name + '" is not allowed'))
            if name == 'type' and len(node.args) == 3:
                violations.append(make_violation(
                    rel_path, node.lineno, 'R2',
                    'three-argument type() builds classes dynamically'))
    return violations


def scan_file(path):
    rel_path = os.path.relpath(path, ROOT)
    text = read_text(path)
    if text is None:
        return []
    violations = scan_lines(rel_path, text)
    if path.endswith('.py'):
        violations.extend(scan_python(rel_path, text))
    return violations


def format_violation(violation):
    return '{0}:{1}: [{2}] {3}'.format(
        violation['path'], violation['line'], violation['rule'],
        violation['message'])


def is_inside_repo(path):
    real_root = os.path.realpath(ROOT)
    real_path = os.path.realpath(path)
    if real_path == real_root:
        return False
    return real_path.startswith(real_root + os.sep)


def is_skipped(path):
    parts = os.path.relpath(os.path.realpath(path),
                            os.path.realpath(ROOT)).split(os.sep)
    for part in parts:
        if part in SKIP_DIRS:
            return True
    return False


def hook_file_path(payload):
    if not isinstance(payload, dict) or 'tool_input' not in payload:
        return ''
    tool_input = payload['tool_input']
    if not isinstance(tool_input, dict):
        return ''
    for key in ['file_path', 'notebook_path']:
        if key in tool_input and isinstance(tool_input[key], str):
            return tool_input[key]
    return ''


def run_hook():
    try:
        payload = json.loads(sys.stdin.read() or '{}')
    except ValueError:
        return 0
    path = hook_file_path(payload)
    if not path or not os.path.isfile(path):
        return 0
    if not is_inside_repo(path) or is_skipped(path):
        return 0
    violations = scan_file(path)
    if not violations:
        return 0
    sys.stderr.write('OLIVER constraint check failed for '
                     + os.path.relpath(path, ROOT) + ':\n')
    for violation in violations:
        sys.stderr.write('  ' + format_violation(violation) + '\n')
    sys.stderr.write('Fix these before continuing (see CLAUDE.md).\n')
    return 2


def expand_targets(paths):
    targets = []
    for path in paths:
        if os.path.isdir(path):
            targets.extend(list_repo_files(os.path.abspath(path)))
        else:
            targets.append(path)
    return targets


def run_scan(paths):
    targets = expand_targets(paths) if paths else list_repo_files(ROOT)
    violations = []
    for path in targets:
        if os.path.isfile(path):
            violations.extend(scan_file(os.path.abspath(path)))
    for violation in violations:
        print(format_violation(violation))
    checked = len(targets)
    if violations:
        print('{0} violation(s) in {1} file(s) checked.'.format(
            len(violations), checked))
        return 1
    print('OK: {0} file(s) checked, 0 violations.'.format(checked))
    return 0


def main(argv):
    if '--hook' in argv:
        return run_hook()
    return run_scan([arg for arg in argv if not arg.startswith('--')])


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
