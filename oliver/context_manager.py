"""Context management for OLIVER: repository index, repo map, memory, compression.

- index_repository() lists files and extracts symbols (tree-sitter for Python,
  with ast and regex fallbacks) so the model gets a map instead of whole files.
- extract_task_hints() and build_repo_map() rank files against the task.
- append_memory() keeps a running list of facts, attempts and failures.
- compress_context() keeps the conversation under the token budget without
  ever separating a tool call from its tool result.
"""

import ast
import os
import re
import subprocess

from oliver import foundation_model

SKIP_DIRS = ['.git', 'node_modules', '.venv', 'venv', 'env', '__pycache__', '.tox', '.mypy_cache',
             '.pytest_cache', '.ruff_cache', 'dist', 'build', '.eggs', 'site-packages', '.idea', '.vscode']
SOURCE_EXTENSIONS = ['.py', '.js', '.jsx', '.ts', '.tsx', '.go', '.rs', '.java', '.rb', '.php', '.c', '.h',
                     '.cc', '.cpp', '.hpp', '.cs', '.kt', '.swift', '.scala']
MAX_INDEXED_FILES = 20000
MAX_SYMBOL_FILE_BYTES = 400000
MEMORY_LIMIT = 40

STOPWORDS = set('''the and for with that this from when then than into onto have has had are was were
will would should could can cannot not but you your our their there here what which while where who
how why all any each some such only also just more most less very about after before because been
being does doing done its it's use used using get gets set sets add adds new old one two three
file files code line lines test tests function method class value values return returns error
issue bug fix fixed expected actual should instead however like make makes made please need needs'''.split())

GENERIC_SYMBOL_PATTERNS = [
    ('function', re.compile(r'^\s*(?:export\s+)?(?:default\s+)?(?:async\s+)?function\*?\s+([A-Za-z_$][\w$]*)')),
    ('class', re.compile(r'^\s*(?:export\s+)?(?:default\s+)?(?:abstract\s+)?(?:public\s+)?class\s+([A-Za-z_$][\w$]*)')),
    ('function', re.compile(r'^\s*func\s+(?:\([^)]*\)\s*)?([A-Za-z_]\w*)')),
    ('function', re.compile(r'^\s*(?:pub(?:\([^)]*\))?\s+)?(?:async\s+)?fn\s+([A-Za-z_]\w*)')),
    ('function', re.compile(r'^\s*def\s+([A-Za-z_]\w*[!?]?)')),
    ('method', re.compile(r'^\s*(?:public|private|protected|internal)\s+(?:static\s+)?[\w<>\[\],.? ]+\s+([A-Za-z_]\w*)\s*\(')),
]

PARSER_CACHE = {}


# --- indexing ------------------------------------------------------------------

def is_skipped_path(rel_path):
    for part in rel_path.split('/'):
        if part in SKIP_DIRS:
            return True
    return False


def list_repo_files(repo_root):
    files = []
    if os.path.isdir(os.path.join(repo_root, '.git')):
        try:
            completed = subprocess.run(
                ['git', 'ls-files', '--cached', '--others', '--exclude-standard', '-z'],
                cwd=repo_root, capture_output=True, timeout=60)
            if completed.returncode == 0:
                for raw in completed.stdout.split(b'\x00'):
                    if raw:
                        files.append(raw.decode('utf-8', 'replace'))
        except (OSError, subprocess.SubprocessError):
            files = []
    if not files:
        for current, dirs, names in os.walk(repo_root):
            dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS and not d.startswith('.'))
            for name in sorted(names):
                full = os.path.join(current, name)
                files.append(os.path.relpath(full, repo_root).replace(os.sep, '/'))
    kept = []
    for rel in sorted(set(files)):
        if is_skipped_path(rel):
            continue
        if not os.path.isfile(os.path.join(repo_root, rel)):
            continue
        kept.append(rel)
        if len(kept) >= MAX_INDEXED_FILES:
            break
    return kept


def is_test_path(rel_path):
    base = os.path.basename(rel_path)
    lowered = rel_path.lower()
    if base.startswith('test_') or base.endswith('_test.py') or base.endswith('_test.go'):
        return True
    if '.test.' in base or '.spec.' in base or base.startswith('conftest'):
        return True
    return '/tests/' in '/' + lowered or '/test/' in '/' + lowered


def index_repository(repo_root):
    files = list_repo_files(repo_root)
    symbols = {}
    languages = {}
    for rel in files:
        extension = os.path.splitext(rel)[1].lower()
        key = extension if extension else '(none)'
        languages[key] = (languages[key] if key in languages else 0) + 1
        if extension not in SOURCE_EXTENSIONS:
            continue
        full = os.path.join(repo_root, rel)
        try:
            if os.stat(full).st_size > MAX_SYMBOL_FILE_BYTES:
                continue
            with open(full, 'r', encoding='utf-8', errors='replace') as handle:
                source = handle.read()
        except OSError:
            continue
        found = extract_symbols(extension, source)
        if found:
            symbols[rel] = found
    return {
        'root': repo_root,
        'files': files,
        'symbols': symbols,
        'languages': languages,
        'test_files': [rel for rel in files if is_test_path(rel)],
    }


def extract_symbols(extension, source):
    if extension == '.py':
        parser = python_parser()
        if parser is not None:
            try:
                return python_symbols_tree_sitter(parser, source.encode('utf-8', 'replace'))
            except Exception:
                pass
        return python_symbols_ast(source)
    return generic_symbols(source)


def python_parser():
    if 'python' in PARSER_CACHE:
        return PARSER_CACHE['python']
    parser = None
    try:
        import tree_sitter
        import tree_sitter_python
        parser = tree_sitter.Parser(tree_sitter.Language(tree_sitter_python.language()))
    except Exception:
        parser = None
    PARSER_CACHE['python'] = parser
    return parser


def python_symbols_tree_sitter(parser, source_bytes):
    tree = parser.parse(source_bytes)
    symbols = []
    collect_tree_sitter_definitions(tree.root_node, '', symbols, 0)
    return symbols


def collect_tree_sitter_definitions(node, prefix, symbols, depth):
    for child in node.children:
        target = child
        if child.type == 'decorated_definition':
            inner = child.child_by_field_name('definition')
            if inner is not None:
                target = inner
        if target.type not in ('function_definition', 'class_definition'):
            continue
        name_node = target.child_by_field_name('name')
        if name_node is None:
            continue
        name = name_node.text.decode('utf-8', 'replace')
        if target.type == 'class_definition':
            kind = 'class'
        else:
            kind = 'method' if prefix else 'function'
        symbols.append({'name': prefix + name, 'kind': kind, 'line': target.start_point[0] + 1})
        if target.type == 'class_definition' and depth == 0:
            body = target.child_by_field_name('body')
            if body is not None:
                collect_tree_sitter_definitions(body, name + '.', symbols, depth + 1)


def python_symbols_ast(source):
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return generic_symbols(source)
    symbols = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            symbols.append({'name': node.name, 'kind': 'function', 'line': node.lineno})
        elif isinstance(node, ast.ClassDef):
            symbols.append({'name': node.name, 'kind': 'class', 'line': node.lineno})
            for inner in node.body:
                if isinstance(inner, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    symbols.append({'name': node.name + '.' + inner.name, 'kind': 'method', 'line': inner.lineno})
    return symbols


def generic_symbols(source):
    symbols = []
    for number, line in enumerate(source.splitlines(), start=1):
        for kind, pattern in GENERIC_SYMBOL_PATTERNS:
            match = pattern.match(line)
            if match:
                symbols.append({'name': match.group(1), 'kind': kind, 'line': number})
                break
    return symbols[:400]


# --- task hints and ranking ----------------------------------------------------

def unique(items):
    seen = []
    for item in items:
        if item not in seen:
            seen.append(item)
    return seen


def extract_task_hints(task_text):
    paths = re.findall(r'[\w./-]+\.(?:py|js|jsx|ts|tsx|go|rs|java|rb|php|c|h|cc|cpp|hpp|cs|kt|toml|cfg|ini|json|yaml|yml)\b',
                       task_text)
    frames = [match[0] for match in re.findall(r'File "([^"]+)", line (\d+)', task_text)]
    tokens = re.findall(r'[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*', task_text)
    identifiers = []
    words = []
    for token in tokens:
        for part in token.split('.'):
            lowered = part.lower()
            if len(part) < 3 or lowered in STOPWORDS:
                continue
            words.append(lowered)
            looks_like_code = '_' in part or '.' in token or re.search(r'[a-z][A-Z]', part) is not None
            if looks_like_code:
                identifiers.append(part)
    errors = re.findall(r'\b([A-Z][A-Za-z]*(?:Error|Exception|Warning))\b', task_text)
    return {
        'paths': unique(paths),
        'frames': unique(frames),
        'identifiers': unique(identifiers),
        'errors': unique(errors),
        'words': unique(words),
    }


def same_file(rel_path, mention):
    """Match whole path components only: "stats.py" must not match "test_stats.py"."""
    mention = mention[2:] if mention.startswith('./') else mention
    return (rel_path == mention or rel_path.endswith('/' + mention)
            or mention.endswith('/' + rel_path))


def score_file(rel_path, symbols, hints):
    lowered = rel_path.lower()
    word_set = set(hints['words'])
    identifier_set = set(hints['identifiers'])
    score = 0.0
    for mention in hints['paths']:
        if same_file(lowered, mention.lower()):
            score += 10.0
    for frame in hints['frames']:
        if same_file(lowered, frame.lower().replace('\\', '/')):
            score += 8.0
    stem = os.path.splitext(os.path.basename(lowered))[0]
    if stem in word_set:
        score += 5.0
    for part in re.split(r'[/_.\-]', lowered):
        if len(part) > 2 and part in word_set:
            score += 1.0
    for symbol in symbols:
        short = symbol['name'].split('.')[-1]
        if short in identifier_set:
            score += 4.0
        elif short.lower() in word_set:
            score += 1.5
    if is_test_path(rel_path):
        score *= 0.6
    return score


def rank_files(index, hints):
    scored = []
    for rel in index['files']:
        symbols = index['symbols'][rel] if rel in index['symbols'] else []
        scored.append((-score_file(rel, symbols, hints), rel))
    scored.sort()
    return [(rel, -negative) for negative, rel in scored]


def build_repo_map(index, hints, max_chars):
    languages = sorted(index['languages'].items(), key=lambda item: -item[1])
    header = 'Repository: {0} files ({1})'.format(
        len(index['files']), ', '.join(name + ' ' + str(count) for name, count in languages[:6]))
    lines = [header, '', 'Most relevant files for this task (ranked by the harness):']
    ranked = rank_files(index, hints)
    shown = []
    for rel, score in ranked:
        if score <= 0 or len(shown) >= 15:
            break
        shown.append(rel)
        lines.append('  ' + rel)
        for symbol in (index['symbols'][rel] if rel in index['symbols'] else [])[:25]:
            lines.append('    {0} {1} (line {2})'.format(symbol['kind'], symbol['name'], symbol['line']))
    if not shown:
        lines.append('  (no strong matches; explore with search_repo)')
    lines.append('')
    lines.append('All files by directory:')
    groups = {}
    for rel in index['files']:
        directory = os.path.dirname(rel) or '.'
        if directory not in groups:
            groups[directory] = []
        groups[directory].append(os.path.basename(rel))
    text = '\n'.join(lines)
    listed = 0
    for directory in sorted(groups):
        entry = '  ' + directory + '/: ' + ', '.join(groups[directory])
        if len(text) + len(entry) + 1 > max_chars:
            text += '\n  ... ({0} more directories; use list_files)'.format(len(groups) - listed)
            break
        text += '\n' + entry
        listed += 1
    return text


# --- working memory --------------------------------------------------------------

def append_memory(memory, kind, text, step):
    text = ' '.join(text.split())
    if not text:
        return memory
    for entry in memory:
        if entry['kind'] == kind and entry['text'] == text:
            return memory
    memory.append({'kind': kind, 'text': text[:600], 'step': step})
    if len(memory) > MEMORY_LIMIT:
        for position, entry in enumerate(memory):
            if entry['kind'] not in ('failure', 'attempt'):
                del memory[position]
                break
        else:
            del memory[0]
    return memory


def render_memory(memory, max_entries=15):
    if not memory:
        return '(empty)'
    return '\n'.join('- [' + entry['kind'] + '] ' + entry['text'] for entry in memory[-max_entries:])


# --- output truncation and compression -------------------------------------------

def truncate_output(text, max_chars):
    if len(text) <= max_chars:
        return text
    head = max_chars * 2 // 3
    tail = max_chars - head
    omitted = len(text) - head - tail
    return text[:head] + '\n[... {0} characters omitted ...]\n'.format(omitted) + text[-tail:]


def message_text(message):
    content = message['content'] if 'content' in message else ''
    if content is None:
        return ''
    if isinstance(content, str):
        return content
    return ' '.join(block['text'] for block in content if isinstance(block, dict) and 'text' in block)


def mask_message(message, limit):
    text = message_text(message)
    if len(text) <= limit:
        return message
    first_line = text.strip().splitlines()[0][:160] if text.strip() else ''
    masked = dict(message)
    masked['content'] = '[older output elided to save context; it began: ' + first_line + ']'
    return masked


def compress_context(messages, model_ctx, budget_tokens, keep_recent, protected, summary_text):
    """Return (messages, stats). Tier 1 masks old tool output and long old turns.
    Tier 2 drops the oldest whole turns after the protected prefix and inserts one
    note carrying the plan and working memory. Tool calls and their results are
    always removed together, so the history stays valid for every provider."""
    before = foundation_model.count_tokens(model_ctx, messages)
    stats = {'compressed': False, 'before': before, 'after': before, 'masked': 0, 'dropped': 0}
    if before <= budget_tokens:
        return messages, stats

    result = [dict(message) for message in messages]
    boundary = max(protected, len(result) - keep_recent)
    for position in range(protected, boundary):
        message = result[position]
        limit = 300 if message['role'] == 'tool' else 1200
        masked = mask_message(message, limit)
        if masked is not message:
            result[position] = masked
            stats['masked'] += 1
    tokens = foundation_model.count_tokens(model_ctx, result)

    note = {'role': 'user', 'content': '[Earlier steps were removed to save context.]\n' + summary_text}
    note_inserted = False
    while tokens > budget_tokens and len(result) - protected > keep_recent:
        start = protected + (1 if note_inserted else 0)
        if start >= len(result) - keep_recent:
            break
        end = start + 1
        first = result[start]
        if first['role'] == 'assistant' and 'tool_calls' in first and first['tool_calls']:
            while end < len(result) and result[end]['role'] == 'tool':
                end += 1
        elif first['role'] == 'tool':
            while end < len(result) and result[end]['role'] == 'tool':
                end += 1
        del result[start:end]
        stats['dropped'] += end - start
        if not note_inserted:
            result.insert(protected, note)
            note_inserted = True
        while protected + 1 < len(result) and result[protected + 1]['role'] == 'tool':
            del result[protected + 1]
            stats['dropped'] += 1
        tokens = foundation_model.count_tokens(model_ctx, result)

    stats['compressed'] = True
    stats['after'] = tokens
    return result, stats
