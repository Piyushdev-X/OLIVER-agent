"""Standalone HTML evidence report for one OLIVER run.

Follows docs/design/DESIGN_LANGUAGE.md: the canonical stylesheet is inlined,
every value is hardcoded, the layout is a fixed 960px page, logos have
dedicated slots, and the run's step-by-step record is a table. All run data is
HTML-escaped before it reaches the page.
"""

import base64
import html
import math
import os

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STYLESHEET = os.path.join(PROJECT_ROOT, 'docs', 'design', 'oliver.css')
LOGO_DIR = os.path.join(PROJECT_ROOT, 'docs', 'assets', 'logos')
LOGOS = [('lcc', 'LCC'), ('devclub', 'DevClub'), ('team', 'Team')]
FALLBACK_CSS = ('body{margin:0;min-width:960px;background:#F4EFE6;color:#1F2328;'
                'font-family:Helvetica,Arial,sans-serif}.page{width:960px;margin:0 auto}')
PHASE_ORDER = ['plan', 'execute', 'review']
STATUS_TEXT = {
    'resolved': ('Resolved', 'is-pass', 'verified by tests'),
    'unverified': ('Unverified', 'is-skip', 'patch not verified'),
    'failed': ('Failed', 'is-fail', 'no usable patch'),
}
BADGES = {
    'passed': ('badge-pass', '✓ Pass'),
    'failed': ('badge-fail', '✕ Fail'),
    'error': ('badge-error', '! Error'),
    'skipped': ('badge-skip', '– Skipped'),
}


def esc(value):
    return html.escape(str(value), quote=True)


def load_stylesheet():
    try:
        with open(STYLESHEET, 'r', encoding='utf-8') as handle:
            return handle.read()
    except OSError:
        return FALLBACK_CSS


def logo_slot(key, label, small):
    size_class = ' logo-slot-sm' if small else ''
    for extension, mime in (('.svg', 'image/svg+xml'), ('.png', 'image/png')):
        path = os.path.join(LOGO_DIR, key + extension)
        if os.path.isfile(path):
            with open(path, 'rb') as handle:
                data = base64.b64encode(handle.read()).decode('ascii')
            return '<div class="logo-slot{0}"><img src="data:{1};base64,{2}" alt="{3}"></div>'.format(
                size_class, mime, data, esc(label))
    suffix = '' if small else ' logo'
    return '<div class="logo-slot{0} is-empty"><span class="logo-slot-label">{1}{2}</span></div>'.format(
        size_class, esc(label), suffix)


def compact_number(value):
    if value >= 1000000:
        return '{0:.1f}M'.format(value / 1000000.0)
    if value >= 1000:
        return '{0:.1f}K'.format(value / 1000.0)
    return str(value)


def format_duration(seconds):
    seconds = int(round(seconds))
    if seconds < 60:
        return '{0}s'.format(seconds)
    return '{0}m {1:02d}s'.format(seconds // 60, seconds % 60)


def badge(status):
    if status in BADGES:
        css, label = BADGES[status]
        return '<span class="badge {0}">{1}</span>'.format(css, label)
    return '<span class="badge">not run</span>'


# --- sections ----------------------------------------------------------------

def header_section(run):
    logos = ''.join(logo_slot(key, label, False) for key, label in LOGOS)
    return ('<header class="site-header"><div class="brand"><p class="wordmark">OLIVER</p>'
            '<p class="wordmark-sub">AI Coding Harness · run report</p></div>'
            '<div class="logo-rail" aria-label="Organization logos">' + logos + '</div></header>')


def kpi_section(run):
    label, dot, note = STATUS_TEXT[run['status']] if run['status'] in STATUS_TEXT else STATUS_TEXT['failed']
    tests_value, tests_note = '—', 'no test command'
    verification = run['verification']
    if verification and verification['tests'] and verification['tests']['collected']:
        counts = verification['tests']['counts']
        total = counts['passed'] + counts['failed'] + counts['error'] + counts['skipped']
        tests_value = '{0} / {1}'.format(counts['passed'] + counts['skipped'], total)
        comparison = verification['comparison']
        tests_note = '{0} fixed, {1} broken'.format(len(comparison['fixed']), len(comparison['broken']))
    usage = run['usage']
    tiles = [
        ('Status', esc(label), '<span class="status"><span class="status-dot {0}"></span>{1}</span>'.format(
            dot, esc(note))),
        ('Tests passing', esc(tests_value), esc(tests_note)),
        ('Tokens used', compact_number(usage['total_tokens']), '{0} model calls'.format(run['model_calls'])),
        ('Wall time', format_duration(run['duration_seconds']), '{0} steps'.format(run['steps'])),
    ]
    parts = ['<div class="kpi-row">']
    for title, value, sub in tiles:
        parts.append('<div class="kpi"><p class="kpi-label">{0}</p><p class="kpi-value">{1}</p>'
                     '<p class="kpi-sub">{2}</p></div>'.format(title, value, sub))
    parts.append('</div>')
    return ''.join(parts)


def nice_ceiling(value):
    if value <= 0:
        return 1
    exponent = 10 ** int(math.floor(math.log10(value)))
    for multiple in (1, 2, 2.5, 5, 10):
        if multiple * exponent >= value:
            return multiple * exponent
    return 10 * exponent


def token_chart(by_phase):
    phases = [phase for phase in PHASE_ORDER if phase in by_phase]
    phases += sorted(phase for phase in by_phase if phase not in PHASE_ORDER)
    rows = [(phase, by_phase[phase]['prompt_tokens'] + by_phase[phase]['completion_tokens']) for phase in phases]
    if not rows:
        return '<p class="caption">No model calls were made.</p>'
    scale = nice_ceiling(max(value for _, value in rows))
    left, right = 110.0, 820.0
    height = len(rows) * 36 + 32
    axis_y = len(rows) * 36 + 4
    parts = ['<div class="chart"><p class="chart-title">Tokens by phase</p>'
             '<p class="chart-sub">Prompt and completion tokens per orchestrator phase. '
             'The token table below is the same data.</p>'
             '<svg width="890" height="{0}" viewBox="0 0 890 {0}" role="img" '
             'aria-label="Bar chart of tokens by phase">'.format(height)]
    for tick in range(5):
        x = left + (right - left) * tick / 4.0
        parts.append('<line class="gridline" x1="{0:.1f}" y1="0" x2="{0:.1f}" y2="{1}"/>'.format(x, axis_y))
        parts.append('<text class="axis-label" x="{0:.1f}" y="{1}" text-anchor="middle">{2}</text>'.format(
            x, axis_y + 20, compact_number(int(scale * tick / 4))))
    for position, (phase, value) in enumerate(rows):
        top = position * 36 + 8
        end = left + (right - left) * value / float(scale)
        rounded = max(end - 4, left)
        parts.append('<text class="category-label" x="0" y="{0}">{1}</text>'.format(top + 12, esc(phase.upper())))
        parts.append('<path class="bar" d="M {0} {1} H {2:.1f} Q {3:.1f} {1} {3:.1f} {4} V {5} Q {3:.1f} {6} {2:.1f} {6} '
                     'H {0} Z"><title>{7}: {8:,} tokens</title></path>'.format(
                         left, top, rounded, max(end, left), top + 4, top + 12, top + 16, esc(phase.upper()), value))
        parts.append('<text class="value-label" x="{0:.1f}" y="{1}">{2:,}</text>'.format(max(end, left) + 8, top + 12, value))
    parts.append('</svg></div>')
    return ''.join(parts)


def token_table(usage):
    rows = []
    for phase in sorted(usage['by_phase']):
        data = usage['by_phase'][phase]
        rows.append('<tr><td class="mono">{0}</td><td class="num">{1}</td><td class="num">{2:,}</td>'
                    '<td class="num">{3:,}</td><td class="num">${4:.4f}</td></tr>'.format(
                        esc(phase.upper()), data['calls'], data['prompt_tokens'], data['completion_tokens'],
                        data['cost']))
    rows.append('<tr><td><strong>Total</strong></td><td class="num">{0}</td><td class="num">{1:,}</td>'
                '<td class="num">{2:,}</td><td class="num">${3:.4f}</td></tr>'.format(
                    sum(usage['by_phase'][phase]['calls'] for phase in usage['by_phase']),
                    usage['prompt_tokens'], usage['completion_tokens'], usage['cost']))
    return ('<table class="table"><thead><tr><th>Phase</th><th class="num">Calls</th><th class="num">Prompt</th>'
            '<th class="num">Completion</th><th class="num">Cost</th></tr></thead><tbody>'
            + ''.join(rows) + '</tbody></table>')


def checks_table(verification):
    if not verification:
        return '<p class="caption">Verification did not run.</p>'
    rows = []
    for check in verification['checks']:
        rows.append('<tr><td class="mono">{0}</td><td>{1}</td><td class="mono">{2}</td></tr>'.format(
            esc(check['name']), badge('passed' if check['ok'] else 'failed'),
            esc(check['detail'][:800]).replace('\n', '<br>')))
    table = ('<table class="table"><thead><tr><th>Check</th><th>Result</th><th>Evidence</th></tr></thead><tbody>'
             + ''.join(rows) + '</tbody></table>')
    if verification['warnings']:
        table += ('<div class="callout"><p class="callout-title">Warnings</p><p>'
                  + '<br>'.join(esc(warning) for warning in verification['warnings']) + '</p></div>')
    return table


def tests_table(run):
    verification = run['verification']
    baseline = run['baseline']
    if not verification or not verification['tests']:
        return '<p class="caption">No per-test results were recorded.</p>'
    final = verification['tests']['outcomes']
    before = baseline['outcomes'] if baseline else {}
    comparison = verification['comparison']
    priority = comparison['fixed'] + comparison['broken'] + comparison['new_failing'] + comparison['new_passing'] \
        + comparison['still_failing']
    ordered = priority + [test for test in sorted(final) if test not in priority]
    rows = []
    for test in ordered[:40]:
        rows.append('<tr><td class="mono">{0}</td><td>{1}</td><td>{2}</td></tr>'.format(
            esc(test), badge(before[test]) if test in before else '<span class="badge">new</span>', badge(final[test])))
    extra = ''
    if len(ordered) > 40:
        extra = '<p class="caption">{0} more tests not shown.</p>'.format(len(ordered) - 40)
    return ('<table class="table"><thead><tr><th>Test</th><th>Baseline</th><th>Final</th></tr></thead><tbody>'
            + ''.join(rows) + '</tbody></table>' + extra)


def diff_block(patch, changed_files):
    if not patch.strip():
        return '<p class="caption">No changes were produced.</p>'
    lines = patch.splitlines()
    parts = ['<div class="code-block"><div class="code-title"><span>patch.diff</span><span>{0} file(s) changed</span>'
             '</div><div class="diff">'.format(len(changed_files))]
    for line in lines[:800]:
        css = 'diff-line'
        if line.startswith(('diff --git', 'index ', '--- ', '+++ ', 'new file mode', 'deleted file mode')):
            css += ' diff-meta'
        elif line.startswith('@@'):
            css += ' diff-hunk'
        elif line.startswith('+'):
            css += ' diff-add'
        elif line.startswith('-'):
            css += ' diff-del'
        parts.append('<div class="{0}">{1}</div>'.format(css, esc(line) if line else ' '))
    if len(lines) > 800:
        parts.append('<div class="diff-line diff-meta">[{0} more lines in patch.diff]</div>'.format(len(lines) - 800))
    parts.append('</div></div>')
    return ''.join(parts)


def trajectory_table(trajectory):
    rows = []
    for entry in trajectory[:200]:
        if entry['kind'] == 'tool':
            action = esc(entry['tool'])
            target = esc(entry['target'])
            outcome = ('<span class="badge badge-pass">✓ OK</span>' if entry['ok']
                       else '<span class="badge badge-fail">✕ Error</span>')
        else:
            action = esc(entry['kind'])
            target = esc(entry['detail'].splitlines()[0][:140] if entry['detail'] else '')
            outcome = '<span class="badge badge-running">… event</span>'
            if entry['kind'] == 'verify':
                outcome = badge('passed' if entry['detail'].startswith('PASSED') else 'failed')
        tokens = '{0:,}'.format(entry['tokens']) if 'tokens' in entry else ''
        rows.append('<tr><td class="num">{0}</td><td class="mono">{1}</td><td class="mono">{2}</td>'
                    '<td class="mono">{3}</td><td>{4}</td><td class="num">{5}</td></tr>'.format(
                        entry['step'], esc(entry['phase']), action, target, outcome, tokens))
    extra = ''
    if len(trajectory) > 200:
        extra = '<p class="caption">{0} more entries in trajectory.jsonl.</p>'.format(len(trajectory) - 200)
    return ('<table class="table"><thead><tr><th class="num">Step</th><th>Phase</th><th>Action</th><th>Target</th>'
            '<th>Outcome</th><th class="num">Tokens</th></tr></thead><tbody>' + ''.join(rows) + '</tbody></table>'
            + extra)


def summary_section(run):
    parts = ['<div class="code-block"><div class="code-title"><span>task</span><span>{0}</span></div><pre>{1}</pre>'
             '</div>'.format(esc(run['model']), esc(run['task'].strip()))]
    facts = []
    if run['plan']:
        facts.append('<p><strong>Root cause (plan):</strong> ' + esc(run['plan']['root_cause']) + '</p>')
    if run['finish_summary']:
        facts.append('<p><strong>OLIVER summary:</strong> ' + esc(run['finish_summary']) + '</p>')
    for key, title in (('stop_reason', 'Stopped'), ('note', 'Note'), ('fatal_error', 'Error')):
        if run[key]:
            facts.append('<p><strong>' + title + ':</strong> ' + esc(run[key]) + '</p>')
    if facts:
        parts.append('<div class="callout callout-info"><p class="callout-title">Run facts</p>' + ''.join(facts)
                     + '</div>')
    return ''.join(parts)


def render_report(run):
    footer_logos = ''.join(logo_slot(key, label, True) for key, label in LOGOS)
    body = [
        '<div class="page">',
        header_section(run),
        '<main class="content">',
        '<section class="hero"><p class="eyebrow">Run report</p><h1>{0}</h1><p class="lead">{1}</p></section>'.format(
            esc(STATUS_TEXT[run['status']][0] if run['status'] in STATUS_TEXT else run['status']),
            esc(run['repo'])),
        kpi_section(run),
        '<h2>Summary</h2>', summary_section(run),
        '<h2>Verification</h2>', checks_table(run['verification']), tests_table(run),
        '<h2>Patch</h2>', diff_block(run['patch'], run['changed_files']),
        '<h2>Token usage</h2>', token_chart(run['usage']['by_phase']), token_table(run['usage']),
        '<h2>Trajectory</h2>', trajectory_table(run['trajectory']),
        '</main>',
        '<footer class="site-footer"><div class="footer-credits"><p><strong>OLIVER</strong> · AI Coding Harness</p>'
        '<p>Evidence report generated by the harness. Every number on this page comes from the run record.</p></div>'
        '<div class="footer-logos" aria-label="Organization logos">' + footer_logos + '</div></footer>',
        '</div>',
    ]
    return ('<!doctype html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n<title>OLIVER · Run report</title>\n'
            '<style>\n' + load_stylesheet() + '\n</style>\n</head>\n<body>\n' + '\n'.join(body) + '\n</body>\n</html>\n')
