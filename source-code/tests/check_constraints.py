"""Constraint checker for the OLIVER repository (run by `make test`).

Every rule below is a hard project constraint. The banned tokens are
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
  R10 no hard-coded credentials: API keys, access tokens or an assigned
      AI_API_KEY value (the key is only ever read from the environment)

Usage:
  python3 source-code/tests/check_constraints.py            scan the whole repository
  python3 source-code/tests/check_constraints.py PATH ...   scan files or directories
"""

import ast
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

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

CREDENTIAL_PATTERNS = [
    re.compile(r'\b' + 's' + r'k-[A-Za-z0-9_-]{20,}'),        # OpenAI, Anthropic, OpenRouter
    re.compile(r'\b' + 'AI' + r'za[0-9A-Za-z_-]{30,}'),       # Google
    re.compile(r'\b' + 'gs' + r'k_[A-Za-z0-9]{20,}'),         # Groq
    re.compile(r'\b' + 'xa' + r'i-[A-Za-z0-9]{20,}'),         # xAI
    re.compile(r'\b' + 'gh' + r'[pousr]_[A-Za-z0-9]{30,}'),   # GitHub
    re.compile(r'\b' + 'AK' + r'IA[0-9A-Z]{16}\b'),           # AWS access key id
    re.compile('AI_API' + r'_KEY\s*=\s*["\']?[A-Za-z0-9_-]{8,}'),  # an assigned key value
]

WEB_EXTENSIONS = ['.html', '.htm', '.css', '.svg', '.js']
SKIP_DIRS = ['.git', '__pycache__', '.venv', 'venv', 'node_modules', 'runs', 'workspace',
             '.pytest_cache', 'build', 'dist', '.mypy_cache', '.ruff_cache']
LOCAL_ONLY_FILES = ['.env']   # git-ignored; may legitimately hold a developer's key


def make_violation(path, line, rule, message):
    return {'path': path, 'line': line, 'rule': rule, 'message': message}


def list_repo_files(root):
    found = []
    for current, dirs, files in os.walk(root):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS)
        for name in sorted(files):
            if name not in LOCAL_ONLY_FILES:
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
        for pattern in CREDENTIAL_PATTERNS:
            if pattern.search(line):
                violations.append(make_violation(
                    rel_path, number, 'R10',
                    'hard-coded credential found; read it from the AI_API_KEY environment variable'))
                break
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
    return run_scan([arg for arg in argv if not arg.startswith('--')])


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
