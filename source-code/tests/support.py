"""Shared helpers for the OLIVER test suite (plain functions, no fixtures)."""

import os
import shutil

from oliver import context_manager
from oliver import orchestrator
from oliver import tools

SOURCE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO_ROOT = os.path.dirname(SOURCE_ROOT)
TASKS_DIR = os.path.join(SOURCE_ROOT, 'eval', 'tasks')
CHECKER = os.path.join(SOURCE_ROOT, 'tests', 'check_constraints.py')


def make_repo(root, files):
    for rel, text in files.items():
        path = os.path.join(str(root), rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'w', encoding='utf-8') as handle:
            handle.write(text)
    return str(root)


def init_tools(repo, test_command=''):
    settings = orchestrator.build_settings({})
    index = context_manager.index_repository(repo)
    tools.set_tool_context(repo, settings, index, test_command)
    return settings


def copy_task(task_name, destination):
    target = os.path.join(str(destination), 'repo')
    shutil.copytree(os.path.join(TASKS_DIR, task_name, 'repo'), target)
    with open(os.path.join(TASKS_DIR, task_name, 'issue.md'), 'r', encoding='utf-8') as handle:
        return target, handle.read()
