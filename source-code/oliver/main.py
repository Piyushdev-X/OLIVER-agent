"""OLIVER command line. The Makefile is the supported way in (see README.md):

    make run                                      interactive evaluation console
    make run REPO=<path or git URL> ISSUE=<file or GitHub issue URL>
                                                  one task, no prompts
    make setup                                    ends with `python -m oliver --check`

The model and every run setting come from configuration-files/oliver.toml and
the credential from the AI_API_KEY environment variable. Each task writes an
evidence bundle under runs/: patch.diff, trajectory.jsonl, verification.json,
result.json, summary.md, report.html.
"""

import argparse
import os
import shutil
import sys

if __package__ in (None, ''):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from oliver import config  # noqa: E402
from oliver import console  # noqa: E402
from oliver import foundation_model  # noqa: E402
from oliver import orchestrator  # noqa: E402
from oliver import report  # noqa: E402

REQUIRED_PACKAGES = ['litellm', 'pydantic', 'tree-sitter', 'tree-sitter-python', 'pytest']


def parse_arguments(argv):
    parser = argparse.ArgumentParser(
        prog='oliver', description='OLIVER, an autonomous coding harness. Without --repo and --issue it opens the '
                                   'interactive evaluation console.')
    parser.add_argument('--repo', default='', help='repository to fix: a local path or a git URL (cloned into '
                                                   'workspace/)')
    parser.add_argument('--issue', default='', help='the issue or test case: a text file, a GitHub issue URL, '
                                                    'or the text')
    parser.add_argument('--test-command', default='', help='command that runs the tests (default: detected)')
    parser.add_argument('--config', default='', help='configuration file (default: '
                                                     'configuration-files/oliver.toml, or OLIVER_CONFIG)')
    parser.add_argument('--model', default='', help='override the configured model; "mock:script.json" runs '
                                                    'offline')
    parser.add_argument('--out', default=config.RUNS_DIR, help='directory for run evidence (default: runs/)')
    parser.add_argument('--check', action='store_true', help='check configuration, credential and dependencies')
    parser.add_argument('--quiet', action='store_true', help='print only the result')
    return parser.parse_args(argv)


def main(argv):
    args = parse_arguments(argv)
    overrides = {'verbose': not args.quiet}
    if args.model:
        overrides['model'] = args.model
    if args.test_command:
        overrides['test_command'] = args.test_command
    try:
        settings, info = config.load_settings(config.config_path(args.config), overrides)
    except ValueError as error:
        sys.stderr.write('Configuration error: ' + str(error) + '\n')
        return 2
    if args.check:
        return check_installation(settings, info)
    if args.repo and args.issue:
        return run_single_task(args, settings, info)
    return console.run_console({'repo': args.repo, 'issue': args.issue, 'out': args.out}, settings, info)


def run_single_task(args, settings, info):
    """Non-interactive mode: exit code 0 when the task is resolved, 1 when not, 2 on setup errors."""
    if info['problem']:
        sys.stderr.write('Cannot start: ' + info['problem'] + '.\n')
        return 2
    for warning in info['warnings']:
        sys.stderr.write('Warning: ' + warning + '.\n')
    try:
        repo = console.prepare_repository(args.repo)
        task = console.resolve_issue(args.issue)
    except ValueError as error:
        sys.stderr.write(str(error) + '\n')
        return 2
    if not args.quiet:
        print('OLIVER · AI Coding Harness · model ' + settings['model'] + ' (from ' + info['model_source'] + ')',
              flush=True)
    kind, message = foundation_model.check_model(settings)
    if kind == 'fatal':
        sys.stderr.write('The provider rejected the credential or the model: ' + message + '\n')
        return 2
    if kind:
        sys.stderr.write('Warning: the model check did not get an answer (' + message + ').\n')
    result = orchestrator.run_agent_loop(task, repo, settings)
    run_dir = report.write_run_artifacts(result, args.out)
    console.print_result(result, run_dir)
    return 0 if result['status'] == 'resolved' else 1


def check_installation(settings, info):
    """`make setup` finishes with this report. Only a broken installation fails it;
    a missing credential is reported, because AI_API_KEY is only needed by `make run`."""
    failures = []
    print('OLIVER installation check')
    print('  Configuration  ' + config.display_path(info['config_path']))
    if info['configured_model'] == config.AUTO_MODEL and info['problem']:
        print('  Model          auto: chosen from the AI_API_KEY provider when the key is set')
    else:
        print('  Model          ' + settings['model'] + '   from ' + info['model_source'])
    if info['key_set']:
        provider = ' (' + info['key_provider'] + ' key format)' if info['key_provider'] else ''
        print('  AI_API_KEY     set' + provider)
    else:
        print('  AI_API_KEY     not set yet: export AI_API_KEY="<PROVIDED_API_KEY>" before `make run`')
    if info['problem'] and info['key_set']:
        print('  Problem        ' + info['problem'])
    for warning in info['warnings']:
        print('  Warning        ' + warning)

    versions, missing = package_versions()
    print('  Python         ' + sys.version.split()[0] + ' at ' + sys.executable)
    print('  Packages       ' + (', '.join(versions) if versions else 'none'))
    if missing:
        failures.append('missing Python packages: ' + ', '.join(missing))
    import_error = import_harness_dependencies()
    if import_error:
        failures.append('a dependency fails to import: ' + import_error)
    git = shutil.which('git')
    print('  git            ' + (git if git else 'NOT FOUND'))
    if not git:
        failures.append('git is required (OLIVER checkpoints every edit with git)')
    ripgrep = shutil.which('rg')
    print('  ripgrep        ' + (ripgrep if ripgrep else 'not found; the built-in search is used'))
    if settings['sandbox'] == 'docker' and not shutil.which('docker'):
        failures.append('sandbox = "docker" needs the docker command')
    if failures:
        for failure in failures:
            print('  FAILED         ' + failure)
        return 1
    print('Ready. Start the harness with: make run')
    return 0


def package_versions():
    from importlib import metadata
    versions, missing = [], []
    for package in REQUIRED_PACKAGES:
        try:
            versions.append(package + ' ' + metadata.version(package))
        except metadata.PackageNotFoundError:
            missing.append(package)
    return versions, missing


def import_harness_dependencies():
    """Import what the harness loads lazily, so a broken install fails at setup, not mid-task."""
    try:
        import litellm  # noqa: F401
        import tree_sitter  # noqa: F401
        import tree_sitter_python  # noqa: F401
    except BaseException as error:
        if isinstance(error, (KeyboardInterrupt, SystemExit)):
            raise
        return type(error).__name__ + ': ' + str(error)[:300]
    return ''


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
