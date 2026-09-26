"""Run OLIVER on every bundled eval task and score it with the hidden tests.

    make test            scripted mock model: offline, deterministic, no credential
    make test REAL=1     the model from configuration-files/oliver.toml, using AI_API_KEY

Two columns are reported on purpose: what the harness claimed (status) and
what the hidden tests say (resolved). Evidence over claims.
"""

import argparse
import glob
import json
import os
import shlex
import shutil
import sys
import tempfile

SOURCE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, SOURCE_ROOT)

from oliver import config  # noqa: E402
from oliver import foundation_model  # noqa: E402
from oliver import orchestrator  # noqa: E402
from oliver import report  # noqa: E402
from oliver import verification  # noqa: E402

TASKS_DIR = os.path.join(SOURCE_ROOT, 'eval', 'tasks')
EVAL_OUT = os.path.join(config.RUNS_DIR, 'eval')


def run_task(task_dir, settings, out_dir):
    name = os.path.basename(task_dir)
    workspace = tempfile.mkdtemp(prefix='oliver-eval-')
    repo = os.path.join(workspace, 'repo')
    shutil.copytree(os.path.join(task_dir, 'repo'), repo)
    with open(os.path.join(task_dir, 'issue.md'), 'r', encoding='utf-8') as handle:
        task = handle.read()
    task_settings = dict(settings)
    if foundation_model.is_mock_model(settings['model']):
        task_settings['model'] = 'mock:' + os.path.join(task_dir, 'mock_script.json')
    result = orchestrator.run_agent_loop(task, repo, task_settings)
    run_dir = report.write_run_artifacts(result, os.path.join(out_dir, name))

    hidden_dir = os.path.join(repo, 'hidden_tests')
    shutil.copytree(os.path.join(task_dir, 'hidden_tests'), hidden_dir)
    hidden_command = shlex.quote(sys.executable) + ' -m pytest -q -p no:cacheprovider hidden_tests'
    hidden = verification.execute_command(hidden_command, repo, 300, task_settings)
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
    }


def eval_settings(args):
    """Mock runs use the built-in defaults; real runs use the configuration file and AI_API_KEY."""
    if not args.real:
        return orchestrator.build_settings({'model': 'mock', 'max_wall_seconds': args.timeout}), ''
    settings, info = config.load_settings(config.config_path(''), {'max_wall_seconds': args.timeout})
    return settings, info['problem']


def main(argv):
    parser = argparse.ArgumentParser(description='OLIVER eval runner')
    parser.add_argument('--real', action='store_true', help='use the configured model and AI_API_KEY')
    parser.add_argument('--task', default='', help='run only this task')
    parser.add_argument('--timeout', type=int, default=900, help='wall-time limit per task, in seconds')
    parser.add_argument('--out', default=EVAL_OUT, help='directory for evidence and results.json')
    args = parser.parse_args(argv)
    try:
        settings, problem = eval_settings(args)
    except ValueError as error:
        sys.stderr.write('Configuration error: ' + str(error) + '\n')
        return 2
    if problem:
        sys.stderr.write('Cannot run the real-model eval: ' + problem + '.\n')
        return 2

    task_dirs = sorted(path for path in glob.glob(os.path.join(TASKS_DIR, '*')) if os.path.isdir(path))
    if args.task:
        task_dirs = [path for path in task_dirs if os.path.basename(path) == args.task]
    label = settings['model'] if args.real else 'scripted mock model'
    print('OLIVER eval: {0} task(s), {1}'.format(len(task_dirs), label), flush=True)
    rows = []
    for task_dir in task_dirs:
        row = run_task(task_dir, settings, args.out)
        rows.append(row)
        print('  {0:18} status={1:10} hidden_tests={2:5} steps={3:3} tokens={4:7,} attempts={5}'.format(
            row['task'], row['status'], 'PASS' if row['resolved'] else 'FAIL', row['steps'], row['tokens'],
            row['attempts']), flush=True)

    resolved = sum(1 for row in rows if row['resolved'])
    summary = {
        'model': label,
        'tasks': len(rows),
        'resolved': resolved,
        'claimed_resolved': sum(1 for row in rows if row['status'] == 'resolved'),
        'total_tokens': sum(row['tokens'] for row in rows),
        'rows': rows,
    }
    os.makedirs(args.out, exist_ok=True)
    results_path = os.path.join(args.out, 'results.json')
    with open(results_path, 'w', encoding='utf-8') as handle:
        json.dump(summary, handle, indent=2)
        handle.write('\n')
    print('Resolved by hidden tests: {0}/{1} (harness claimed {2}). Results: {3}'.format(
        resolved, len(rows), summary['claimed_resolved'], config.display_path(results_path)))
    return 0 if rows and resolved == len(rows) else 1


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
