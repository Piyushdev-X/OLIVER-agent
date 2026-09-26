"""Run OLIVER on every eval task and score it with the hidden tests.

    python3 eval/run_eval.py                     # scripted mock model, offline
    OLIVER_MODEL=openai/gpt-4o python3 eval/run_eval.py --real
    python3 eval/run_eval.py --sample-report docs/sample-report.html

Two columns are reported on purpose: what the harness claimed (status) and
what the hidden tests say (resolved). Evidence over claims.
"""

import argparse
import glob
import json
import os
import shutil
import sys
import tempfile

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from oliver import main as cli  # noqa: E402
from oliver import orchestrator  # noqa: E402
from oliver import report  # noqa: E402
from oliver import verification  # noqa: E402

TASKS_DIR = os.path.join(PROJECT_ROOT, 'eval', 'tasks')


def run_task(task_dir, args):
    name = os.path.basename(task_dir)
    workspace = tempfile.mkdtemp(prefix='oliver-eval-')
    repo = os.path.join(workspace, 'repo')
    shutil.copytree(os.path.join(task_dir, 'repo'), repo)
    with open(os.path.join(task_dir, 'issue.md'), 'r', encoding='utf-8') as handle:
        task = handle.read()
    model = args.model if args.real else 'mock:' + os.path.join(task_dir, 'mock_script.json')
    settings = orchestrator.build_settings({'model': model, 'verbose': False, 'max_wall_seconds': args.timeout})
    result = orchestrator.run_agent_loop(task, repo, settings)
    run_dir = cli.write_run_artifacts(result, os.path.join(args.out, name))

    hidden_dir = os.path.join(repo, 'hidden_tests')
    shutil.copytree(os.path.join(task_dir, 'hidden_tests'), hidden_dir)
    hidden = verification.execute_command('python3 -m pytest -q -p no:cacheprovider hidden_tests', repo, 300,
                                          settings)
    shutil.rmtree(workspace, ignore_errors=True)
    return {
        'task': name,
        'status': result['status'],
        'resolved': hidden['exit_code'] == 0,
        'steps': result['steps'],
        'tokens': result['usage']['total_tokens'],
        'cost': result['usage']['cost'],
        'attempts': result['attempts'],
        'seconds': result['duration_seconds'],
        'run_dir': run_dir,
        'result': result,
    }


def main(argv):
    parser = argparse.ArgumentParser(description='OLIVER eval runner')
    parser.add_argument('--real', action='store_true', help='use a real model (OLIVER_MODEL or --model)')
    parser.add_argument('--model', default=cli.env_value('OLIVER_MODEL', ''))
    parser.add_argument('--task', default='', help='run only this task')
    parser.add_argument('--timeout', type=int, default=900)
    parser.add_argument('--out', default=os.path.join(PROJECT_ROOT, 'runs', 'eval'))
    parser.add_argument('--results', default=os.path.join(PROJECT_ROOT, 'eval', 'results.json'))
    parser.add_argument('--sample-report', default='', help='also write the first task report to this path')
    args = parser.parse_args(argv)
    if args.real and not args.model:
        sys.stderr.write('--real needs OLIVER_MODEL or --model\n')
        return 2

    task_dirs = sorted(path for path in glob.glob(os.path.join(TASKS_DIR, '*')) if os.path.isdir(path))
    if args.task:
        task_dirs = [path for path in task_dirs if os.path.basename(path) == args.task]
    rows = []
    for task_dir in task_dirs:
        row = run_task(task_dir, args)
        rows.append(row)
        print('{0:18} status={1:10} hidden_tests={2:5} steps={3:3} tokens={4:7,} attempts={5}'.format(
            row['task'], row['status'], 'PASS' if row['resolved'] else 'FAIL', row['steps'], row['tokens'],
            row['attempts']), flush=True)

    if args.sample_report and rows:
        with open(args.sample_report, 'w', encoding='utf-8') as handle:
            handle.write(report.render_report(rows[0]['result']))
    resolved = sum(1 for row in rows if row['resolved'])
    summary = {
        'model': 'real:' + args.model if args.real else 'scripted mock',
        'tasks': len(rows),
        'resolved': resolved,
        'claimed_resolved': sum(1 for row in rows if row['status'] == 'resolved'),
        'total_tokens': sum(row['tokens'] for row in rows),
        'rows': [{key: row[key] for key in row if key not in ('result', 'run_dir')} for row in rows],
    }
    with open(args.results, 'w', encoding='utf-8') as handle:
        json.dump(summary, handle, indent=2)
        handle.write('\n')
    print('Resolved by hidden tests: {0}/{1} (harness claimed {2})'.format(
        resolved, len(rows), summary['claimed_resolved']))
    return 0 if resolved == len(rows) else 1


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
