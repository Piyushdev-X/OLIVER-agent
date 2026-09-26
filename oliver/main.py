"""OLIVER command line entry point.

    python3 -m oliver --repo path/to/repo --task-file issue.md
    OLIVER_MODEL=anthropic/claude-sonnet-4-5 python3 -m oliver --repo . --task "Fix ..."
    python3 -m oliver --repo path --task-file issue.md --model mock:script.json   (offline)

Each run writes an evidence bundle under --out (default runs/):
patch.diff, trajectory.jsonl, verification.json, result.json, summary.md, report.html.
"""

import argparse
import datetime
import json
import os
import re
import sys

if __package__ in (None, ''):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from oliver import orchestrator  # noqa: E402
from oliver import report  # noqa: E402
from oliver import verification  # noqa: E402


def env_value(name, default):
    return os.environ[name] if name in os.environ and os.environ[name] else default


def parse_arguments(argv):
    parser = argparse.ArgumentParser(prog='oliver', description='OLIVER - autonomous coding harness')
    parser.add_argument('--repo', required=True, help='repository to work in')
    task_group = parser.add_mutually_exclusive_group(required=True)
    task_group.add_argument('--task', help='task text')
    task_group.add_argument('--task-file', help='file containing the task (issue text)')
    parser.add_argument('--model', default=env_value('OLIVER_MODEL', ''),
                        help='LiteLLM model id behind the OLIVER alias (env OLIVER_MODEL); "mock" or '
                             '"mock:script.json" runs offline')
    parser.add_argument('--api-base', default=env_value('OLIVER_API_BASE', ''), help='custom API base URL')
    parser.add_argument('--tool-mode', choices=['auto', 'native', 'text'], default=env_value('OLIVER_TOOL_MODE', 'auto'))
    parser.add_argument('--sandbox', choices=['local', 'docker'], default=env_value('OLIVER_SANDBOX', 'local'))
    parser.add_argument('--docker-image', default=env_value('OLIVER_DOCKER_IMAGE', 'python:3.11-slim'))
    parser.add_argument('--test-command', default='', help='override the detected test command')
    parser.add_argument('--max-steps', type=int, default=60, help='maximum model turns')
    parser.add_argument('--max-tokens', type=int, default=2000000, help='maximum total tokens')
    parser.add_argument('--timeout', type=int, default=1800, help='maximum wall time in seconds')
    parser.add_argument('--context-tokens', type=int, default=120000, help='context budget for the conversation')
    parser.add_argument('--no-review', action='store_true', help='skip the post-verification review')
    parser.add_argument('--no-baseline', action='store_true', help='skip the baseline test run')
    parser.add_argument('--out', default='runs', help='directory for run artifacts')
    parser.add_argument('--quiet', action='store_true', help='only print the final summary')
    return parser.parse_args(argv)


def settings_from_arguments(args):
    return {
        'model': args.model,
        'api_base': args.api_base,
        'tool_mode': args.tool_mode,
        'sandbox': args.sandbox,
        'docker_image': args.docker_image,
        'test_command': args.test_command,
        'max_steps': args.max_steps,
        'max_total_tokens': args.max_tokens,
        'max_wall_seconds': args.timeout,
        'context_tokens': args.context_tokens,
        'review': not args.no_review,
        'run_baseline': not args.no_baseline,
        'verbose': not args.quiet,
    }


def read_task(args):
    if args.task:
        return args.task
    with open(args.task_file, 'r', encoding='utf-8') as handle:
        return handle.read()


def run_identifier(task):
    first_line = task.strip().splitlines()[0] if task.strip() else 'task'
    slug = re.sub(r'[^a-z0-9]+', '-', first_line.lower()).strip('-')[:40] or 'task'
    return datetime.datetime.now().strftime('%Y%m%d-%H%M%S') + '-' + slug


def write_text(path, text):
    with open(path, 'w', encoding='utf-8') as handle:
        handle.write(text)


def write_run_artifacts(result, out_dir):
    run_dir = os.path.join(out_dir, run_identifier(result['task']))
    os.makedirs(run_dir, exist_ok=True)
    write_text(os.path.join(run_dir, 'patch.diff'), result['patch'])
    with open(os.path.join(run_dir, 'trajectory.jsonl'), 'w', encoding='utf-8') as handle:
        for entry in result['trajectory']:
            handle.write(json.dumps(entry) + '\n')
    evidence = {'baseline': result['baseline'], 'final': result['verification'],
                'history': result['verifications']}
    write_text(os.path.join(run_dir, 'verification.json'), json.dumps(evidence, indent=2))
    compact = dict(result)
    compact['trajectory'] = '(see trajectory.jsonl)'
    compact['patch'] = '(see patch.diff)'
    write_text(os.path.join(run_dir, 'result.json'), json.dumps(compact, indent=2, default=str))
    write_text(os.path.join(run_dir, 'summary.md'), summary_markdown(result))
    write_text(os.path.join(run_dir, 'report.html'), report.render_report(result))
    return run_dir


def summary_markdown(result):
    usage = result['usage']
    lines = [
        '# OLIVER run: ' + result['status'].upper(),
        '',
        '- Repository: `' + result['repo'] + '`',
        '- Model: `' + result['model'] + '` (backend: ' + result['backend'] + ')',
        '- Steps: {0} model turns, {1} calls, {2} recovery attempts, {3} replans'.format(
            result['steps'], result['model_calls'], result['attempts'], result['replans']),
        '- Tokens: {0:,} (prompt {1:,}, completion {2:,}, cached {3:,}); cost ${4:.4f}'.format(
            usage['total_tokens'], usage['prompt_tokens'], usage['completion_tokens'], usage['cached_tokens'],
            usage['cost']),
        '- Duration: {0}s'.format(result['duration_seconds']),
    ]
    for key, title in (('stop_reason', 'Stopped'), ('note', 'Note'), ('fatal_error', 'Error')):
        if result[key]:
            lines.append('- ' + title + ': ' + result[key])
    lines += ['', '## Task', '', result['task'].strip(), '']
    if result['plan']:
        lines += ['## Plan', '', '- Root cause: ' + result['plan']['root_cause']]
        lines += ['- Step: ' + step for step in result['plan']['steps']]
        lines.append('')
    if result['finish_summary']:
        lines += ['## OLIVER summary', '', result['finish_summary'], '']
    lines += ['## Verification', '']
    if result['verification']:
        lines += ['    ' + line for line in verification.describe_verification(result['verification']).splitlines()]
    else:
        lines.append('Verification did not run.')
    lines += ['', '## Changed files', '']
    lines += ['- `' + item['path'] + '` (' + item['status'] + ')' for item in result['changed_files']] or ['(none)']
    return '\n'.join(lines) + '\n'


def main(argv):
    args = parse_arguments(argv)
    if not args.model:
        sys.stderr.write('No model configured. Set OLIVER_MODEL (for example anthropic/claude-sonnet-4-5 or '
                         'openai/gpt-4o) or pass --model. Use --model mock for an offline dry run.\n')
        return 2
    if not os.path.isdir(args.repo):
        sys.stderr.write('Repository not found: ' + args.repo + '\n')
        return 2
    task = read_task(args)
    settings = orchestrator.build_settings(settings_from_arguments(args))
    if not args.quiet:
        print('OLIVER · AI Coding Harness · model alias OLIVER -> ' + settings['model'], flush=True)
    result = orchestrator.run_agent_loop(task, args.repo, settings)
    run_dir = write_run_artifacts(result, args.out)
    print('Status: {0} | steps {1} | tokens {2:,} | {3}s'.format(
        result['status'].upper(), result['steps'], result['usage']['total_tokens'], result['duration_seconds']))
    if result['verification']:
        print(verification.describe_verification(result['verification']))
    print('Evidence: ' + run_dir)
    return 0 if result['status'] == 'resolved' else 1


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
