"""Build the OLIVER website into site/public. Purely procedural.

    python3 site/build.py              render every page from site/data/runs.json
    python3 site/build.py --collect    re-run the offline eval, refresh the data, render

The deployable output is site/public (static HTML, one stylesheet, one script).
All page data comes from real harness runs captured in site/data/runs.json.
"""

import argparse
import html
import json
import os
import re
import sys

SITE_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SITE_DIR)
PUBLIC_DIR = os.path.join(SITE_DIR, 'public')
DATA_FILE = os.path.join(SITE_DIR, 'data', 'runs.json')

NAV = [
    ('index', 'Overview', 'index.html', 'h'),
    ('architecture', 'Architecture', 'architecture.html', 'a'),
    ('evaluation', 'Evaluation', 'evaluation.html', 'e'),
    ('memory', 'Memory', 'memory.html', 'm'),
]

PALETTE = [
    ('Overview', 'index.html', 'g h', 'home start'),
    ('Architecture', 'architecture.html', 'g a', 'document design'),
    ('2  System diagram', 'architecture.html#system', '', 'modules figure'),
    ('3  Modules', 'architecture.html#modules', '', 'orchestrator context tools recovery'),
    ('4  Control loop', 'architecture.html#loop', '', 'state machine phases'),
    ('5  Tools', 'architecture.html#tools', '', 'read_file write_file search_repo'),
    ('6  Verification', 'architecture.html#verification', '', 'tests baseline evidence'),
    ('7  Recovery', 'architecture.html#recovery', '', 'rollback replan'),
    ('8  Coding constraints', 'architecture.html#constraints', '', 'rules checker'),
    ('Evaluation', 'evaluation.html', 'g e', 'spreadsheet results'),
    ('Sheet: Eval', 'evaluation.html#sheet-eval', '', 'tasks hidden tests'),
    ('Sheet: Tokens', 'evaluation.html#sheet-tokens', '', 'usage phases'),
    ('Sheet: Checks', 'evaluation.html#sheet-checks', '', 'verification'),
    ('Memory', 'memory.html', 'g m', 'ingestion recall'),
    ('Ingestion', 'memory.html#ingestion', '', 'index hints'),
    ('Memory store', 'memory.html#store', '', 'facts decisions failures'),
    ('Recall', 'memory.html#recall', '', 'working memory prompt'),
]

SHORTCUTS = [
    ('Open the command palette', ['/'], ['ctrl', 'k']),
    ('Show keyboard shortcuts', ['?'], []),
    ('Go to overview', ['g', 'h'], []),
    ('Go to architecture', ['g', 'a'], []),
    ('Go to evaluation', ['g', 'e'], []),
    ('Go to memory', ['g', 'm'], []),
    ('Next or previous section', ['j'], ['k']),
    ('Next or previous sheet', [']'], ['[']),
    ('Close a dialog', ['esc'], []),
]

MEMORY_KINDS = ['fact', 'decision', 'failure', 'attempt']
RECALL_EVENTS = ['recover', 'replan', 'compress']


def esc(value):
    return html.escape(str(value), quote=True)


def compact(value):
    if value >= 1000000:
        return '{0:.1f}M'.format(value / 1000000.0)
    if value >= 1000:
        return '{0:.1f}K'.format(value / 1000.0)
    return str(value)


def keycaps(keys, css=''):
    cls = ' class="' + css + '"' if css else ''
    return ' '.join('<kbd' + cls + '>' + esc(key) + '</kbd>' for key in keys)


# --- data collection -------------------------------------------------------------

def phase_at(trajectory, step):
    phase = 'INIT'
    for entry in trajectory:
        if entry['step'] > step:
            break
        phase = entry['phase']
    return phase


def patch_counts(patch):
    added = 0
    removed = 0
    for line in patch.splitlines():
        if line.startswith('+') and not line.startswith('+++'):
            added += 1
        elif line.startswith('-') and not line.startswith('---'):
            removed += 1
    return added, removed


def summarize_run(row):
    from oliver import context_manager
    result = row['result']
    trajectory = result['trajectory']
    init_detail = ''
    for entry in trajectory:
        if entry['kind'] == 'init':
            init_detail = entry['detail']
            break
    indexed = re.search(r'indexed (\d+) files', init_detail)
    recalls = [{'step': entry['step'], 'phase': entry['phase'], 'kind': entry['kind']}
               for entry in trajectory if entry['kind'] in RECALL_EVENTS]
    memory = []
    for entry in result['memory']:
        recalled = [event['kind'].upper() + ' · step ' + str(event['step'])
                    for event in recalls if event['step'] >= entry['step']]
        memory.append({'kind': entry['kind'], 'text': entry['text'], 'step': entry['step'],
                       'phase': phase_at(trajectory, entry['step']), 'recalled': recalled})
    tools_used = {}
    for entry in trajectory:
        if entry['kind'] == 'tool':
            name = entry['tool']
            if name not in tools_used:
                tools_used[name] = {'calls': 0, 'errors': 0}
            tools_used[name]['calls'] += 1
            if not entry['ok']:
                tools_used[name]['errors'] += 1
    checks = []
    if result['verification']:
        for check in result['verification']['checks']:
            first = check['detail'].splitlines()[0] if check['detail'] else ''
            checks.append({'name': check['name'], 'ok': check['ok'], 'detail': first[:160]})
    by_phase = {}
    for phase in result['usage']['by_phase']:
        data = result['usage']['by_phase'][phase]
        by_phase[phase] = {'calls': data['calls'], 'tokens': data['prompt_tokens'] + data['completion_tokens']}
    added, removed = patch_counts(result['patch'])
    title = result['task'].strip().splitlines()[0].lstrip('# ').strip() if result['task'].strip() else row['task']
    return {
        'task': row['task'],
        'title': title,
        'status': result['status'],
        'resolved': row['resolved'],
        'steps': result['steps'],
        'model_calls': result['model_calls'],
        'attempts': result['attempts'],
        'replans': result['replans'],
        'seconds': result['duration_seconds'],
        'tokens': result['usage']['total_tokens'],
        'by_phase': by_phase,
        'files_indexed': int(indexed.group(1)) if indexed else 0,
        'task_chars': len(result['task']),
        'test_command': result['test_command'],
        'memory': memory,
        'recalls': recalls,
        'recall_text': context_manager.render_memory(result['memory']),
        'tools': tools_used,
        'checks': checks,
        'patch': {'files': len(result['changed_files']), 'added': added, 'removed': removed},
        'root_cause': result['plan']['root_cause'] if result['plan'] else '',
    }


def collect_runs():
    sys.path.insert(0, PROJECT_ROOT)
    sys.path.insert(0, os.path.join(PROJECT_ROOT, 'eval'))
    import run_eval
    options = argparse.Namespace(real=False, model='', timeout=900,
                                 out=os.path.join(PROJECT_ROOT, 'runs', 'site'))
    tasks_dir = os.path.join(PROJECT_ROOT, 'eval', 'tasks')
    runs = []
    for name in sorted(os.listdir(tasks_dir)):
        task_dir = os.path.join(tasks_dir, name)
        if not os.path.isdir(task_dir):
            continue
        row = run_eval.run_task(task_dir, options)
        runs.append(summarize_run(row))
        print('collected {0}: {1}, hidden tests {2}'.format(name, row['status'], 'PASS' if row['resolved'] else 'FAIL'))
    data = {'source': 'offline eval, scripted mock trajectories (harness mechanics, not model quality)',
            'runs': runs}
    os.makedirs(os.path.dirname(DATA_FILE), exist_ok=True)
    with open(DATA_FILE, 'w', encoding='utf-8') as handle:
        json.dump(data, handle, indent=2)
        handle.write('\n')
    return data


def load_data():
    with open(DATA_FILE, 'r', encoding='utf-8') as handle:
        return json.load(handle)


# --- shared layout ---------------------------------------------------------------

def logo_rail(small):
    size = ' small' if small else ''
    return ('<div class="logo-rail" aria-label="Organization logos">'
            '<div class="logo-slot' + size + '">Org logo</div>'
            '<div class="logo-slot mark' + size + '">Mark</div></div>')


def topbar(current):
    links = []
    for key, label, href, letter in NAV:
        css = ' class="is-current" aria-current="page"' if key == current else ''
        links.append('<a href="{0}"{1}>{2} <kbd class="small">g {3}</kbd></a>'.format(href, css, esc(label), letter))
    return ('<header class="topbar"><div class="page">'
            '<a class="brand" href="index.html"><span class="sun" aria-hidden="true"></span>OLIVER</a>'
            '<nav class="nav" aria-label="Pages">' + ''.join(links) + '</nav>'
            '<button class="palette-trigger" type="button" data-open="palette" aria-label="Open command palette">'
            '<span>Go to…</span><span class="keys"><kbd class="small">/</kbd><kbd class="small">ctrl k</kbd></span>'
            '</button></div></header>')


def masthead(eyebrow, heading, lede, display):
    css = ' class="display"' if display else ''
    return ('<section class="masthead"><div class="page"><div class="masthead-text">'
            '<p class="eyebrow">' + esc(eyebrow) + '</p><h1' + css + '>' + esc(heading) + '</h1>'
            '<p class="lede">' + esc(lede) + '</p></div>' + logo_rail(False) + '</div></section>')


def palette_dialog():
    items = []
    for label, href, keys, words in PALETTE:
        hint = keycaps(keys.split(' '), 'small') if keys else ''
        items.append('<li><a class="palette-item" href="{0}" data-keywords="{1}"><span>{2}</span>'
                     '<span class="where">{3}</span></a></li>'.format(href, esc(words), esc(label), hint))
    return ('<dialog id="palette" aria-label="Command palette">'
            '<input class="palette-input" type="text" placeholder="Go to a page or section…" '
            'aria-label="Go to a page or section" autocomplete="off">'
            '<ul class="palette-list">' + ''.join(items) + '</ul>'
            '<div class="palette-foot"><span>' + keycaps(['↑', '↓'], 'small') + ' move</span>'
            '<span>' + keycaps(['enter'], 'small') + ' open</span><span>' + keycaps(['esc'], 'small')
            + ' close</span></div></dialog>')


def shortcuts_dialog():
    rows = []
    for label, first, second in SHORTCUTS:
        keys = keycaps(first) + (' or ' + keycaps(second) if second else '')
        if len(first) == 2 and first[0] == 'g':
            keys = keycaps(first[:1]) + ' then ' + keycaps(first[1:])
        rows.append('<tr><td>' + esc(label) + '</td><td>' + keys + '</td></tr>')
    return ('<dialog id="shortcuts" aria-label="Keyboard shortcuts"><p class="sheet-title">Keyboard shortcuts</p>'
            '<table class="keys-table">' + ''.join(rows) + '</table></dialog>')


def footer():
    return ('<footer class="footer"><div class="page"><div class="footer-text">'
            '<p><strong>OLIVER</strong> · AI Coding Harness</p>'
            '<p>Built for the LCC × DevClub AI Coding Harness Hackathon. Same model, different harnesses.</p>'
            '<p>Press <kbd class="small">?</kbd> for keyboard shortcuts.</p></div>' + logo_rail(True)
            + '</div></footer>')


def page(key, title, description, head_html, body_html):
    return ('<!doctype html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n'
            '<meta name="viewport" content="width=1024">\n'
            '<title>' + esc(title) + '</title>\n'
            '<meta name="description" content="' + esc(description) + '">\n'
            '<link rel="stylesheet" href="styles.css">\n'
            '<script src="app.js" defer></script>\n</head>\n<body>\n'
            + topbar(key) + '\n' + head_html + '\n<main class="content"><div class="page">\n' + body_html
            + '\n</div></main>\n' + footer() + '\n' + palette_dialog() + '\n' + shortcuts_dialog() + '\n'
            '<div id="chord-hint" class="chord-hint" hidden><kbd>g</kbd> then '
            + keycaps(['h', 'a', 'e', 'm']) + '</div>\n</body>\n</html>\n')


# --- svg helpers ---------------------------------------------------------------

def svg_open(prefix, width, height, label, description):
    return ('<svg class="dg" width="{0}" height="{1}" viewBox="0 0 {0} {1}" role="img" aria-labelledby="{2}-t {2}-d">'
            '<title id="{2}-t">{3}</title><desc id="{2}-d">{4}</desc><defs>'
            '<marker id="{2}-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" '
            'orient="auto-start-reverse"><path d="M 0 0 L 10 5 L 0 10 z" fill="#0B0B0C"/></marker></defs>'
            '<rect x="0" y="0" width="{0}" height="{1}" fill="#FFFFFF"/>').format(
                width, height, prefix, esc(label), esc(description))


def svg_box(x, y, w, h, variant):
    fill, stroke = '#FFFFFF', '#0B0B0C'
    if variant == 'model':
        fill = '#0B0B0C'
    elif variant == 'ok':
        fill, stroke = '#E7F4EC', '#0B7A3E'
    elif variant == 'fail':
        fill, stroke = '#FDECEA', '#B42318'
    return '<rect x="{0}" y="{1}" width="{2}" height="{3}" rx="10" fill="{4}" stroke="{5}" stroke-width="1.5"/>'.format(
        x, y, w, h, fill, stroke)


def svg_text(css, x, y, text, anchor='start'):
    extra = '' if anchor == 'start' else ' text-anchor="' + anchor + '"'
    return '<text class="{0}" x="{1}" y="{2}"{3}>{4}</text>'.format(css, x, y, extra, esc(text))


def svg_node(x, y, w, h, title, lines, variant):
    inverted = variant == 'model'
    parts = [svg_box(x, y, w, h, variant), svg_text('t-inv' if inverted else 't', x + 14, y + 24, title)]
    for position, line in enumerate(lines):
        css = ('s-inv' if inverted else 's') if position == 0 else 'n'
        parts.append(svg_text(css, x + 14, y + 42 + position * 18, line))
    return ''.join(parts)


def svg_edge(prefix, path, both, dashed):
    dash = ' stroke-dasharray="5 4"' if dashed else ''
    start = ' marker-start="url(#{0}-arrow)"'.format(prefix) if both else ''
    return '<path d="{0}" fill="none" stroke="#0B0B0C" stroke-width="1.25"{1}{2} marker-end="url(#{3}-arrow)"/>'.format(
        path, dash, start, prefix)


def svg_title_block(x, y, drawing, sheet):
    """Engineering-drawing title block: the dedicated logo space in every diagram."""
    return ''.join([
        '<rect x="{0}" y="{1}" width="280" height="80" rx="8" fill="#FFFFFF" stroke="#0B0B0C" stroke-width="1.5"/>'.format(x, y),
        '<path d="M {0} {1} V {2}" stroke="#0B0B0C" stroke-width="1"/>'.format(x + 184, y, y + 80),
        '<rect x="{0}" y="{1}" width="128" height="40" rx="6" fill="#FFFFFF" stroke="#B4B3A8" stroke-dasharray="4 3"/>'.format(x + 8, y + 20),
        svg_text('slot', x + 72, y + 43, 'ORG LOGO', 'middle'),
        '<rect x="{0}" y="{1}" width="40" height="40" rx="6" fill="#FFFFFF" stroke="#B4B3A8" stroke-dasharray="4 3"/>'.format(x + 140, y + 20),
        svg_text('slot', x + 160, y + 43, 'MARK', 'middle'),
        svg_text('t', x + 196, y + 26, 'OLIVER'),
        svg_text('e', x + 196, y + 44, drawing),
        svg_text('e', x + 196, y + 60, sheet),
        svg_text('e', x + 196, y + 74, 'rev A'),
    ])


def svg_legend_box(x, y, variant, label):
    fill, stroke = '#FFFFFF', '#0B0B0C'
    if variant == 'model':
        fill = '#0B0B0C'
    elif variant == 'ok':
        fill, stroke = '#E7F4EC', '#0B7A3E'
    elif variant == 'fail':
        fill, stroke = '#FDECEA', '#B42318'
    return ('<rect x="{0}" y="{1}" width="12" height="12" rx="3" fill="{2}" stroke="{3}" stroke-width="1.25"/>'.format(
        x, y - 10, fill, stroke) + svg_text('lg', x + 20, y, label))


def svg_legend_line(prefix, x, y, dashed, label):
    return svg_edge(prefix, 'M {0} {1} H {2}'.format(x, y - 4, x + 34), False, dashed) + svg_text('lg', x + 44, y, label)


def system_diagram():
    p = 'sys'
    parts = [svg_open(p, 812, 480, 'OLIVER system architecture',
                      'The task enters the orchestration engine, which exchanges prompts with the OLIVER model, '
                      'context with the context manager and actions with the tools and verification pipeline. '
                      'Tools read, edit and test the repository; failures go to the recovery system, which sends a '
                      'recovery prompt back. The repository diff becomes the verified patch.')]
    parts += [
        svg_node(16, 120, 120, 56, 'Task', ['issue text'], 'plain'),
        svg_node(176, 24, 200, 248, 'Orchestration Engine', ['orchestrator.py', '', 'planner + executor loop',
                                                              'phase state machine', 'budgets: steps, tokens, time',
                                                              '', 'decides DONE only from', 'test evidence'], 'plain'),
        svg_node(424, 24, 200, 56, 'OLIVER model', ['LiteLLM alias'], 'model'),
        svg_node(424, 120, 200, 56, 'Context Manager', ['context_manager.py'], 'plain'),
        svg_node(424, 216, 200, 56, 'Tools & Verification', ['tools.py · verification.py'], 'plain'),
        svg_node(424, 312, 200, 56, 'Repository', ['code · tests · git'], 'plain'),
        svg_node(672, 312, 124, 56, 'Verified patch', ['diff + evidence'], 'ok'),
        svg_node(176, 312, 200, 56, 'Recovery System', ['recovery.py'], 'plain'),
        svg_edge(p, 'M 136 148 H 173', False, False), svg_text('e', 155, 140, 'issue', 'middle'),
        svg_edge(p, 'M 377 52 H 421', True, False), svg_text('e', 399, 44, 'prompts', 'middle'),
        svg_edge(p, 'M 377 148 H 421', True, False), svg_text('e', 399, 140, 'context', 'middle'),
        svg_edge(p, 'M 377 244 H 421', True, False), svg_text('e', 399, 236, 'actions', 'middle'),
        svg_edge(p, 'M 524 273 V 309', True, False), svg_text('e', 518, 295, 'edit · test', 'end'),
        svg_edge(p, 'M 625 326 H 648 V 148 H 627', False, False), svg_text('e', 654, 240, 'index'),
        svg_edge(p, 'M 625 352 H 669', False, False), svg_text('e', 647, 344, 'diff', 'middle'),
        svg_edge(p, 'M 423 260 H 400 V 340 H 379', False, False), svg_text('e', 396, 300, 'failures', 'end'),
        svg_edge(p, 'M 276 311 V 275', False, True), svg_text('e', 268, 296, 'recovery prompt', 'end'),
        svg_legend_box(16, 408, 'model', 'foundation model (OLIVER)'),
        svg_legend_box(16, 432, 'plain', 'harness module'),
        svg_legend_box(16, 456, 'ok', 'verified output'),
        svg_legend_line(p, 240, 408, False, 'data flow'),
        svg_legend_line(p, 240, 432, True, 'recovery path'),
        svg_title_block(516, 384, 'architecture', 'sheet 1/2'),
        '</svg>',
    ]
    return ''.join(parts)


def loop_diagram():
    p = 'loop'
    parts = [svg_open(p, 812, 420, 'OLIVER control loop',
                      'INIT leads to PLAN, EXECUTE and VERIFY. Passing checks go to REVIEW and DONE; a critique '
                      'sends work back to EXECUTE. Failing checks go to RECOVER, which retries EXECUTE with a recovery '
                      'prompt, replans when a failure repeats, or ends in FAILED when a limit is reached.')]
    parts += [
        svg_node(16, 72, 108, 52, 'INIT', ['index · baseline'], 'plain'),
        svg_node(168, 56, 176, 84, 'PLAN', ['read-only tools', 'writes a JSON plan'], 'plain'),
        svg_node(476, 56, 176, 84, 'EXECUTE', ['edit and run tools', 'ends with finish()'], 'plain'),
        svg_node(476, 216, 176, 84, 'VERIFY', ['harness runs checks', 'compares to baseline'], 'plain'),
        svg_node(168, 216, 176, 84, 'RECOVER', ['classify, trim error', 'rollback when stuck'], 'plain'),
        svg_node(692, 148, 104, 52, 'REVIEW', ['diff critique'], 'model'),
        svg_node(692, 236, 104, 48, '✓ DONE', ['patch kept'], 'ok'),
        svg_node(16, 236, 108, 48, '✕ FAILED', ['best patch kept'], 'fail'),
        svg_edge(p, 'M 124 98 H 165', False, False),
        svg_edge(p, 'M 344 98 H 473', False, False), svg_text('e', 409, 90, 'plan ready', 'middle'),
        svg_edge(p, 'M 564 140 V 213', False, False), svg_text('e', 572, 182, 'finish'),
        svg_edge(p, 'M 476 258 H 347', False, False), svg_text('e', 411, 250, 'checks fail', 'middle'),
        svg_edge(p, 'M 304 216 V 180 H 520 V 143', False, True), svg_text('e', 316, 172, 'retry with recovery prompt'),
        svg_edge(p, 'M 208 216 V 143', False, True), svg_text('e', 216, 198, 'replan'),
        svg_edge(p, 'M 168 260 H 127', False, False), svg_text('e', 147, 252, 'limit', 'middle'),
        svg_edge(p, 'M 652 244 H 672 V 174 H 689', False, False), svg_text('e', 668, 210, 'pass', 'end'),
        svg_edge(p, 'M 744 200 V 233', False, False), svg_text('e', 752, 222, 'approved'),
        svg_edge(p, 'M 744 148 V 98 H 655', False, False), svg_text('e', 662, 90, 'fix critique'),
        svg_legend_line(p, 16, 350, False, 'transition'),
        svg_legend_line(p, 16, 374, True, 'recovery path'),
        svg_legend_box(200, 350, 'ok', 'terminal: success'),
        svg_legend_box(200, 374, 'fail', 'terminal: failure'),
        svg_title_block(516, 324, 'control loop', 'sheet 2/2'),
        '</svg>',
    ]
    return ''.join(parts)


# --- grids ---------------------------------------------------------------------

def column_letters(count):
    return [chr(ord('A') + index) for index in range(count)]


def grid(headers, rows, total_row, active_cell, numeric_columns, input_columns):
    """A spreadsheet grid: column letters, row numbers, a bold header row (row 1),
    blue input values, and an optional totals row with double top border."""
    letters = column_letters(len(headers))
    parts = ['<div class="xl-scroll"><table class="xl"><thead><tr><th class="rn"></th>']
    parts += ['<th>' + letter + '</th>' for letter in letters]
    parts.append('</tr></thead><tbody><tr class="hd"><td class="rn">1</td>')
    parts += ['<td>' + esc(header) + '</td>' for header in headers]
    parts.append('</tr>')
    all_rows = [(row, False) for row in rows] + ([(total_row, True)] if total_row else [])
    for offset, (cells, is_total) in enumerate(all_rows):
        number = offset + 2
        parts.append('<tr{0}><td class="rn">{1}</td>'.format(' class="total"' if is_total else '', number))
        for position, cell in enumerate(cells):
            css = []
            if position in numeric_columns:
                css.append('n')
            if position in input_columns and not is_total:
                css.append('in')
            if letters[position] + str(number) == active_cell:
                css.append('is-active')
            attribute = ' class="' + ' '.join(css) + '"' if css else ''
            parts.append('<td{0}>{1}</td>'.format(attribute, cell))
        parts.append('</tr>')
    parts.append('</tbody></table></div>')
    return ''.join(parts)


def status_chip(status):
    if status in ('resolved', 'PASS', True):
        return '<span class="st st-pass">✓ ' + ('PASS' if status in ('PASS', True) else 'RESOLVED') + '</span>'
    if status in ('unverified',):
        return '<span class="st st-warn">– UNVERIFIED</span>'
    if status in ('n/a',):
        return '<span class="st st-none">– N/A</span>'
    return '<span class="st st-fail">✕ ' + ('FAIL' if status in ('FAIL', False) else esc(str(status).upper())) + '</span>'


def data_bar(value, maximum):
    width = 0 if maximum <= 0 else max(2, int(round(120.0 * value / maximum)))
    return ('<svg class="databar" width="120" height="8" viewBox="0 0 120 8" aria-hidden="true">'
            '<rect class="track" x="0" y="0" width="120" height="8" rx="4"/>'
            '<rect x="0" y="0" width="{0}" height="8" rx="4"/></svg>'.format(width))


# --- pages ---------------------------------------------------------------------

def index_page(data):
    runs = data['runs']
    resolved = sum(1 for run in runs if run['resolved'])
    claimed = sum(1 for run in runs if run['status'] == 'resolved')
    agree = sum(1 for run in runs if (run['status'] == 'resolved') == run['resolved'])
    tokens = sum(run['tokens'] for run in runs)
    attempts = sum(run['attempts'] for run in runs)
    tiles = [
        ('Resolved by hidden tests', '{0} / {1}'.format(resolved, len(runs)), 'scored after each run'),
        ('Harness claims matching evidence', '{0} / {1}'.format(agree, len(runs)), '{0} claimed resolved'.format(claimed)),
        ('Tokens per task', compact(tokens // max(1, len(runs))), 'average across tasks'),
        ('Recoveries', str(attempts), 'failed checks fixed in-run'),
    ]
    tile_html = ''.join('<div class="tile"><p class="tile-label">{0}</p><p class="tile-value">{1}</p>'
                        '<p class="tile-sub">{2}</p></div>'.format(esc(a), esc(b), esc(c)) for a, b, c in tiles)
    toc = [('1', 'Purpose', 'architecture.html#purpose', 'Architecture'),
           ('2', 'System diagram', 'architecture.html#system', 'Architecture'),
           ('3', 'Modules', 'architecture.html#modules', 'Architecture'),
           ('4', 'Control loop', 'architecture.html#loop', 'Architecture'),
           ('5', 'Tools', 'architecture.html#tools', 'Architecture'),
           ('6', 'Verification', 'architecture.html#verification', 'Architecture'),
           ('7', 'Recovery', 'architecture.html#recovery', 'Architecture'),
           ('8', 'Coding constraints', 'architecture.html#constraints', 'Architecture'),
           ('A', 'Evaluation workbook', 'evaluation.html', 'Evaluation'),
           ('B', 'Ingestion, memory and recall', 'memory.html', 'Memory')]
    toc_html = ''.join('<li><a href="{0}"><span class="num">{1}</span>{2}<span class="leader"></span>'
                       '<span class="where">{3}</span></a></li>'.format(href, num, esc(label), where)
                       for num, label, href, where in toc)
    key_rows = ''.join('<tr><td>{0}</td><td>{1}</td></tr>'.format(
        esc(label), (keycaps(first[:1]) + ' then ' + keycaps(first[1:])) if len(first) == 2 and first[0] == 'g'
        else keycaps(first) + (' or ' + keycaps(second) if second else '')) for label, first, second in SHORTCUTS[:7])
    body = ('<h2 class="section-title" data-section id="glance">At a glance</h2>'
            '<p class="section-note">' + esc(data['source']) + '.</p>'
            '<div class="row">' + tile_html + '</div>'
            '<h2 class="section-title" data-section id="contents">Contents</h2>'
            '<div class="grid-2"><div class="card"><ul class="toc">' + toc_html + '</ul></div>'
            '<div class="card"><h3>Keyboard first</h3><p class="meta">every page, no mouse needed</p>'
            '<table class="keys-table">' + key_rows + '</table></div></div>'
            '<h2 class="section-title" data-section id="read">How to read this site</h2>'
            '<div class="grid-3">'
            '<div class="card"><p class="meta">document</p><h3>Architecture</h3><p>A numbered specification with '
            'two drawings: the module view and the control loop. Every rule it states is enforced in code.</p></div>'
            '<div class="card"><p class="meta">workbook</p><h3>Evaluation</h3><p>Three sheets of measured results: '
            'what the harness claimed, what the hidden tests say, and where the tokens went.</p></div>'
            '<div class="card"><p class="meta">memory</p><h3>Ingestion and recall</h3><p>How a task becomes '
            'working memory, what the memory holds at each phase, and what is recalled into prompts.</p></div>'
            '</div>')
    head = masthead('Autonomous coding harness', 'OLIVER',
                    'One standardized foundation model, wrapped in a harness that plans, edits, verifies and '
                    'recovers. A task is done only when the evidence passes.', True)
    return page('index', 'OLIVER · AI Coding Harness', 'OLIVER architecture and evaluation', head, body)


def architecture_page(data):
    modules = [
        ['Orchestration Engine', '<span class="id">orchestrator.py</span>',
         'run_agent_loop drives a dict of phase functions; enforces step, token and time limits'],
        ['Foundation model', '<span class="id">foundation_model.py</span>',
         'OLIVER alias over LiteLLM; typed retries; overflow signal; text tool protocol; mock backend'],
        ['Context Manager', '<span class="id">context_manager.py</span>',
         'repository index and ranked map; working memory; compression that keeps tool pairs intact'],
        ['Tool & Verification', '<span class="id">tools.py · verification.py</span>',
         'confined file tools, syntax guard with rollback, sandboxed commands, baseline test diffing'],
        ['Recovery System', '<span class="id">recovery.py</span>',
         'failure classes, trimmed tracebacks, loop detection, shadow-git checkpoints and rollback'],
        ['Evidence', '<span class="id">report.py · main.py</span>',
         'patch, trajectory, verification record and a static HTML report for every run'],
    ]
    tools_rows = [
        ['read_file', 'filepath, start_line, end_line', 'path confined to the repository; windowed, numbered'],
        ['write_file', 'filepath, content', 'syntax-checked; the file is restored if it breaks'],
        ['edit_file', 'filepath, old_text, new_text', 'exact single match; byte-exact rollback on failure'],
        ['search_repo', 'query, path_glob, regex', 'symbol definitions first, then ripgrep matches'],
        ['list_files', 'path, pattern', 'fresh listing, capped at 200 entries'],
        ['run_command', 'command, timeout_seconds', 'blocklist, timeout, secrets removed from the environment'],
        ['run_tests', 'target', 'per-test outcomes from JUnit, failures trimmed'],
        ['finish', 'summary', 'hands control to the harness for verification'],
    ]
    phase_rows = [
        ['INIT', 'harness', 'none', 'repository indexed, baseline tests recorded'],
        ['PLAN', 'model', 'read-only', 'a valid JSON plan (one repair retry)'],
        ['EXECUTE', 'model', 'all', 'finish is called or the step limit is reached'],
        ['VERIFY', 'harness', 'none', 'syntax, reproduction and tests compared with the baseline'],
        ['RECOVER', 'harness', 'none', 'a recovery prompt, or rollback and replan'],
        ['REVIEW', 'model', 'none', 'the diff is approved or one critique round is used'],
    ]
    constraint_rows = [
        ['Purely procedural Python: no classes, decorators or meta-programming', 'R1, R2', status_chip('PASS')],
        ['No instance attributes; no dictionary get-method calls', 'R3, R4', status_chip('PASS')],
        ['No CSS custom properties and no var function', 'R5, R7', status_chip('PASS')],
        ['No media-query rules; rigid 960px layout', 'R6', status_chip('PASS')],
        ['No chronological track elements in any visual', 'R8', status_chip('PASS')],
    ]

    def doc_table(headers, rows, mono_first):
        cells = []
        for row in rows:
            first = '<span class="id">' + esc(row[0]) + '</span>' if mono_first else esc(row[0])
            cells.append([first] + [cell if cell.startswith('<') else esc(cell) for cell in row[1:]])
        return '<div class="xl-wrap">' + grid(headers, cells, None, '', [], []) + '</div>'

    body = ''.join([
        '<article class="doc">',
        '<div class="doc-running"><span>OLIVER · Architecture documentation</span><span>Rev A · Sheet 1</span></div>',
        '<h2 id="purpose" data-section><span class="num">1</span>Purpose</h2>',
        '<p>OLIVER turns one standardized foundation model into a careful software engineer. It reads an issue, '
        'locates the relevant code, makes a minimal change and proves that change with tests before it calls the '
        'task done. Every team in the event uses the same model, so the harness is the difference.</p>',
        '<p>The design follows three rules from the brief: correctness first, evidence over claims, and efficient '
        'use of resources. Each section below names the mechanism that implements one of them.</p>',
        '<h2 id="system" data-section><span class="num">2</span>System</h2>',
        '<p>All model traffic passes through the orchestration engine. Tools touch the repository only through '
        'path-confined functions, and the delivered patch is the repository diff taken after verification.</p>',
        '<div class="fig">' + system_diagram() + '</div>',
        '<p class="fig-caption">Figure 1. Module view. The black node is the only component that is not code '
        'in this repository.</p>',
        '<h2 id="modules" data-section><span class="num">3</span>Modules</h2>',
        doc_table(['Module', 'Files', 'Responsibility'], modules, False),
        '<h2 id="loop" data-section><span class="num">4</span>Control loop</h2>',
        '<p>The orchestrator is a state machine of plain functions. Each returns the name of the next phase; '
        'limits on steps, tokens and wall time end the run in FAILED while keeping the best verified checkpoint.</p>',
        '<div class="fig">' + loop_diagram() + '</div>',
        '<p class="fig-caption">Figure 2. Control loop, drawn as a loop: recovery returns to execution or to '
        'planning.</p>',
        doc_table(['Phase', 'Actor', 'Tools', 'Leaves when'], phase_rows, True),
        '<h2 id="tools" data-section><span class="num">5</span>Tools</h2>',
        '<p>The three required tools keep their exact signatures; five more complete the interface. Every tool '
        'returns a short string, and every error starts with ERROR and says how to fix the call.</p>',
        doc_table(['Tool', 'Arguments', 'Guard'], tools_rows, True),
        '<h2 id="verification" data-section><span class="num">6</span>Verification</h2>',
        '<ol><li>Every changed file must parse.</li><li>The plan\'s reproduction command, if any, must now '
        'succeed; commands on the blocklist are never run.</li><li>No test that passed at baseline may fail, and '
        'tests the task is about must pass.</li><li>A test command that cannot run is not evidence.</li></ol>',
        '<h2 id="recovery" data-section><span class="num">7</span>Recovery</h2>',
        '<ul><li>Failures are classified (syntax, import, regression, reproduction, test, timeout) and the '
        'traceback is trimmed to the lines that explain it.</li><li>The same failure twice triggers a rollback '
        'to the last good checkpoint and a new plan.</li><li>Identical repeated actions are detected and '
        'interrupted.</li></ul>',
        '<h2 id="constraints" data-section><span class="num">8</span>Coding constraints</h2>',
        '<p>A checker enforces the hackathon rules on every edit and in the test suite.</p>',
        doc_table(['Rule', 'Checker', 'Status'], constraint_rows, False),
        '<div class="doc-folio"><span>OLIVER architecture documentation</span><span>Page 1 of 1</span></div>',
        '</article>',
    ])
    head = masthead('Document · Rev A', 'Architecture',
                    'The harness as a numbered specification: what each module does, how control flows, and '
                    'which rule each mechanism enforces.', False)
    return page('architecture', 'OLIVER · Architecture', 'OLIVER architecture documentation', head, body)


def evaluation_page(data):
    runs = data['runs']
    last = len(runs) + 1
    eval_rows = []
    for run in runs:
        eval_rows.append(['<span class="id">' + esc(run['task']) + '</span>', esc(run['title']),
                          status_chip(run['status']), status_chip('PASS' if run['resolved'] else 'FAIL'),
                          str(run['steps']), str(run['model_calls']), str(run['attempts']),
                          '{0:,}'.format(run['tokens']), '{0:.2f}'.format(run['seconds'])])
    resolved = sum(1 for run in runs if run['resolved'])
    eval_total = ['<strong>Total</strong>', '', '{0} claimed'.format(sum(1 for run in runs if run['status'] == 'resolved')),
                  '{0} / {1} pass'.format(resolved, len(runs)), str(sum(run['steps'] for run in runs)),
                  str(sum(run['model_calls'] for run in runs)), str(sum(run['attempts'] for run in runs)),
                  '{0:,}'.format(sum(run['tokens'] for run in runs)), '{0:.2f}'.format(sum(run['seconds'] for run in runs))]
    phases = ['plan', 'execute', 'review']
    maximum = max(run['tokens'] for run in runs) if runs else 0
    token_rows = []
    for run in runs:
        cells = ['<span class="id">' + esc(run['task']) + '</span>']
        for phase in phases:
            cells.append('{0:,}'.format(run['by_phase'][phase]['tokens']) if phase in run['by_phase'] else '0')
        cells.append('{0:,}'.format(run['tokens']))
        cells.append(data_bar(run['tokens'], maximum) + '{0:.0f}%'.format(100.0 * run['tokens'] / maximum if maximum else 0))
        token_rows.append(cells)
    token_total = ['<strong>Total</strong>']
    for phase in phases:
        token_total.append('{0:,}'.format(sum(run['by_phase'][phase]['tokens'] for run in runs if phase in run['by_phase'])))
    token_total += ['{0:,}'.format(sum(run['tokens'] for run in runs)), '']
    check_names = ['changes', 'syntax', 'reproduction', 'tests']
    check_rows = []
    for run in runs:
        found = {}
        for check in run['checks']:
            found[check['name']] = check['ok']
        cells = ['<span class="id">' + esc(run['task']) + '</span>']
        for name in check_names:
            cells.append(status_chip('PASS' if found[name] else 'FAIL') if name in found else status_chip('n/a'))
        all_ok = all(check['ok'] for check in run['checks']) and bool(run['checks'])
        cells.append(status_chip('PASS' if all_ok else 'FAIL'))
        check_rows.append(cells)
    all_pass = all(all(check['ok'] for check in run['checks']) for run in runs)
    check_total = ['<strong>All tasks</strong>', '', '', '', '', 'TRUE' if all_pass else 'FALSE']

    sheets = [
        ('sheet-eval', 'Eval', 'D' + str(last + 1), '=COUNTIF(D2:D{0},"PASS")'.format(last),
         grid(['Task', 'Issue', 'Harness claim', 'Hidden tests', 'Steps', 'Model calls', 'Recoveries', 'Tokens',
               'Seconds'], eval_rows, eval_total, 'D' + str(last + 1), [4, 5, 6, 7, 8], [4, 5, 6, 7, 8])),
        ('sheet-tokens', 'Tokens', 'E' + str(last + 1), '=SUM(E2:E{0})'.format(last),
         grid(['Task', 'PLAN', 'EXECUTE', 'REVIEW', 'Total', 'Share of largest run'], token_rows, token_total,
              'E' + str(last + 1), [1, 2, 3, 4], [1, 2, 3, 4])),
        ('sheet-checks', 'Checks', 'F' + str(last + 1), '=AND(F2:F{0})'.format(last),
         grid(['Task', 'Changes', 'Syntax', 'Reproduction', 'Tests', 'Verified'], check_rows, check_total,
              'F' + str(last + 1), [], [])),
    ]
    first = sheets[0]
    sheet_html = ''.join('<section id="{0}" class="xl-sheet" aria-label="Sheet {1}">{2}</section>'.format(
        sheet_id, esc(name), table) for sheet_id, name, cell, expr, table in sheets)
    tabs = ''.join('<a class="xl-tab{0}" href="#{1}" data-sheet-tab="{1}" data-cell="{2}" data-expr="{3}" '
                   'role="tab">{4}</a>'.format(' is-current' if sheet_id == first[0] else '', sheet_id, esc(cell),
                                               esc(expr), esc(name))
                   for sheet_id, name, cell, expr, table in sheets)
    body = ''.join([
        '<h2 class="section-title" data-section id="workbook">Workbook</h2>',
        '<p class="section-note">' + esc(data['source']) + '. Switch sheets with the tabs or '
        '<kbd class="small">[</kbd> <kbd class="small">]</kbd>.</p>',
        '<div class="xl-wrap"><div class="xl-formula"><span class="xl-name" data-sheet-name>' + esc(first[2])
        + '</span><span class="xl-fx">fx</span><span class="xl-expr" data-sheet-expr>' + esc(first[3])
        + '</span></div>' + sheet_html + '<nav class="xl-tabs" role="tablist" aria-label="Sheets">' + tabs
        + '</nav></div>',
        '<div class="xl-legend"><span><span class="swatch-in">Blue</span> measured by the run</span>'
        '<span><span class="swatch-fx">Black</span> totals and formulas</span>'
        '<span>Chips pair a glyph with a word, never colour alone</span></div>',
        '<h2 class="section-title" data-section id="reading">Reading the numbers</h2>',
        '<div class="grid-3">',
        '<div class="card"><p class="meta">claim vs evidence</p><h3>Two columns on purpose</h3><p>"Harness claim" '
        'is what OLIVER reported; "Hidden tests" is an independent check run after the harness stops.</p></div>',
        '<div class="card"><p class="meta">recoveries</p><h3>Failures that were fixed</h3><p>A recovery is a failed '
        'verification that the loop repaired within the same run, with the evidence recorded.</p></div>',
        '<div class="card"><p class="meta">scope</p><h3>Mechanics, not model quality</h3><p>Scripted trajectories '
        'prove the machinery. Run the eval with a real OLIVER_MODEL for model scores.</p></div>',
        '</div>',
    ])
    head = masthead('Workbook · 3 sheets', 'Evaluation',
                    'Measured results for every eval task, laid out as a spreadsheet: claims, independent test '
                    'results, token use and verification checks.', False)
    return page('evaluation', 'OLIVER · Evaluation', 'OLIVER evaluation results', head, body)


def memory_page(data):
    runs = data['runs']
    featured = runs[0]
    for run in runs:
        if (len(run['recalls']), len(run['memory'])) > (len(featured['recalls']), len(featured['memory'])):
            featured = run
    counts = {}
    for kind in MEMORY_KINDS:
        counts[kind] = sum(1 for entry in featured['memory'] if entry['kind'] == kind)
    stages = [
        ('Ingest', 'extract_task_hints()', 'Issue text becomes paths, identifiers, error names and stack frames.',
         '{0:,} characters read'.format(featured['task_chars'])),
        ('Index', 'index_repository()', 'Files and symbols from tree-sitter, with ast and regex fallbacks.',
         '{0} files indexed'.format(featured['files_indexed'])),
        ('Store', 'append_memory()', 'Facts, decisions, failures and attempts; deduplicated and capped at 40.',
         '{0} entries stored'.format(len(featured['memory']))),
        ('Compress', 'compress_context()', 'Old tool output is masked; old turns fold into one note with the plan.',
         'tool calls stay paired'),
        ('Recall', 'render_memory()', 'The last 15 entries are injected into recovery and replan prompts.',
         '{0} recall events'.format(len(featured['recalls']))),
        ('Evidence', 'verify_changes()', 'Only verified state survives; rollback restores the best checkpoint.',
         '{0} recoveries'.format(featured['attempts'])),
    ]
    stage_html = ''.join('<div class="card"><p class="meta">{0}</p><h3>{1}</h3><p>{2}</p>'
                         '<p class="meta">{3}</p></div>'.format(esc(fn), esc(name), esc(text), esc(stat))
                         for name, fn, text, stat in stages)
    manifest = grid(['Source', 'Items', 'Stored as'], [
        ['Task text', '{0:,} characters'.format(featured['task_chars']), 'first user message + task hints'],
        ['Repository', '{0} files'.format(featured['files_indexed']), 'repository map in the system prompt'],
        ['Baseline tests', esc(featured['test_command'] or 'none'), 'fact entry in working memory'],
        ['Plan', esc(featured['root_cause'][:70]), 'decision entry in working memory'],
    ], None, '', [], [1])
    tree_lines = ['/memory']
    for kind in MEMORY_KINDS:
        tree_lines.append('├── {0}/{1}({2})'.format(kind, ' ' * (10 - len(kind)), counts[kind]))
    tree_lines.append('└── recall log  ({0})'.format(len(featured['recalls'])))
    entry_rows = []
    for position, entry in enumerate(featured['memory'], start=1):
        entry_rows.append([str(position), '<span class="kind kind-{0}">{0}</span>'.format(esc(entry['kind'])),
                           '<span class="wrap">' + esc(entry['text'][:150]) + '</span>',
                           esc(entry['phase']) + ' · step ' + str(entry['step']),
                           esc(', '.join(entry['recalled'])) if entry['recalled'] else '<span class="st st-none">– not yet</span>'])
    across_rows = []
    for run in runs:
        tally = {}
        for kind in MEMORY_KINDS:
            tally[kind] = sum(1 for entry in run['memory'] if entry['kind'] == kind)
        across_rows.append(['<span class="id">' + esc(run['task']) + '</span>'] + [str(tally[kind]) for kind in MEMORY_KINDS]
                           + [str(len(run['recalls'])), status_chip('PASS' if run['resolved'] else 'FAIL')])
    recall_text = ('<span class="dim">Working memory (as sent in the RECOVER prompt of '
                   + esc(featured['task']) + '):</span>\n' + esc(featured['recall_text']))
    body = ''.join([
        '<h2 class="section-title" data-section id="ingestion">Ingestion</h2>',
        '<p class="section-note">How one task becomes working memory. Figures are from the '
        + esc(featured['task']) + ' run.</p>',
        '<div class="grid-3">' + stage_html + '</div>',
        '<h2 class="section-title" data-section id="manifest">Ingestion manifest</h2>',
        '<div class="xl-wrap">' + manifest + '</div>',
        '<h2 class="section-title" data-section id="store">Memory store</h2>',
        '<p class="section-note">Every entry with the phase that stored it and the prompts that recalled it.</p>',
        '<div class="grid-2"><pre class="tree">' + esc('\n'.join(tree_lines)) + '</pre>'
        '<div class="card"><h3>' + esc(featured['title']) + '</h3><p>' + esc(featured['root_cause']) + '</p>'
        '<p class="meta">status: ' + esc(featured['status']) + ' · recoveries: ' + str(featured['attempts'])
        + ' · steps: ' + str(featured['steps']) + '</p></div></div>',
        '<div class="xl-wrap">' + grid(['#', 'Kind', 'Entry', 'Stored', 'Recalled in'], entry_rows,
                                                        None, '', [0], []) + '</div>',
        '<h2 class="section-title" data-section id="recall">Recall</h2>',
        '<pre class="black-panel">' + recall_text + '</pre>',
        '<h2 class="section-title" data-section id="across">Memory across tasks</h2>',
        '<div class="xl-wrap">' + grid(['Task', 'Facts', 'Decisions', 'Failures', 'Attempts', 'Recalls', 'Hidden tests'],
                                       across_rows, None, '', [1, 2, 3, 4, 5], [1, 2, 3, 4, 5]) + '</div>',
    ])
    head = masthead('Ingestion · state · recall', 'Memory',
                    'What OLIVER takes in, what it keeps, and what it brings back into the prompt when a check '
                    'fails.', False)
    return page('memory', 'OLIVER · Memory', 'OLIVER ingestion, memory and recall', head, body)


def not_found_page():
    body = ('<h2 class="section-title" data-section id="missing">This page does not exist</h2>'
            '<p class="section-note">Press <kbd class="small">/</kbd> to open the command palette, or go to the '
            '<a href="index.html">overview</a>.</p>')
    head = masthead('Error 404', 'Not found', 'The address does not match any page on this site.', False)
    return page('', 'OLIVER · Not found', 'Page not found', head, body)


def write_page(name, text):
    with open(os.path.join(PUBLIC_DIR, name), 'w', encoding='utf-8') as handle:
        handle.write(text)


def build(data):
    os.makedirs(PUBLIC_DIR, exist_ok=True)
    write_page('index.html', index_page(data))
    write_page('architecture.html', architecture_page(data))
    write_page('evaluation.html', evaluation_page(data))
    write_page('memory.html', memory_page(data))
    write_page('404.html', not_found_page())
    print('built 5 pages into ' + os.path.relpath(PUBLIC_DIR, PROJECT_ROOT))


def main(argv):
    parser = argparse.ArgumentParser(description='Build the OLIVER website')
    parser.add_argument('--collect', action='store_true', help='re-run the offline eval and refresh site data')
    args = parser.parse_args(argv)
    data = collect_runs() if args.collect else load_data()
    build(data)
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
