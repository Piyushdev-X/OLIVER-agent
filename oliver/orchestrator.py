"""The OLIVER orchestration engine.

run_agent_loop(task, repo_path) drives a small state machine held in one dict:

    INIT -> PLAN -> EXECUTE -> VERIFY -> REVIEW -> DONE
                      ^          |         |
                      |          v         |
                      +------ RECOVER <----+   (RECOVER may also go back to PLAN)

Each phase is a plain function that takes the state dict and returns the name
of the next phase. The harness, not the model, decides when the task is done:
only a passing verification can lead to DONE. Budgets on steps, tokens and wall
time end the run in FAILED, keeping the best verified checkpoint if one exists.
"""

import json
import os
import re
import time
import traceback

from oliver import context_manager
from oliver import foundation_model
from oliver import prompts
from oliver import recovery
from oliver import tools
from oliver import verification

DEFAULT_SETTINGS = {
    'model': 'mock',
    'api_base': '',
    'tool_mode': 'auto',
    'temperature': 0.0,
    'max_output_tokens': 8192,
    'request_timeout': 300,
    'max_retries': 4,
    'retry_base_seconds': 1.0,
    'context_tokens': 120000,
    'compress_ratio': 0.75,
    'keep_recent_messages': 12,
    'max_steps': 60,
    'max_total_tokens': 2000000,
    'max_wall_seconds': 1800,
    'plan_steps': 10,
    'execute_steps': 30,
    'max_recovery_attempts': 3,
    'max_replans': 1,
    'review': True,
    'max_review_rounds': 1,
    'loop_threshold': 3,
    'sandbox': 'local',
    'docker_image': 'python:3.11-slim',
    'command_timeout': 120,
    'test_timeout': 600,
    'test_command': '',
    'run_baseline': True,
    'read_window': 400,
    'max_tool_output_chars': 12000,
    'repo_map_chars': 8000,
    'verbose': False,
}

TERMINAL_PHASES = ['DONE', 'FAILED']


def build_settings(overrides):
    settings = dict(DEFAULT_SETTINGS)
    for key in overrides if overrides else {}:
        if key not in DEFAULT_SETTINGS:
            raise ValueError('unknown setting: ' + key)
        settings[key] = overrides[key]
    return settings


def new_state(task, repo_path, settings):
    return {
        'task': task,
        'repo_path': os.path.realpath(repo_path),
        'settings': settings,
        'phase': 'INIT',
        'messages': [],
        'memory': [],
        'plan': None,
        'step': 0,
        'model_calls': 0,
        'usage': {'prompt_tokens': 0, 'completion_tokens': 0, 'cached_tokens': 0, 'cost': 0.0, 'by_phase': {}},
        'trajectory': [],
        'started_at': time.monotonic(),
        'index': None,
        'hints': None,
        'test_command': '',
        'baseline': None,
        'snapshot': None,
        'model_ctx': None,
        'verification': None,
        'verifications': [],
        'attempts': 0,
        'replans': 0,
        'review_rounds': 0,
        'failure_signatures': [],
        'action_history': [],
        'loop_warnings': 0,
        'execute_prompted': False,
        'replan_note': '',
        'best_checkpoint': '',
        'best_verification': None,
        'stop_reason': '',
        'fatal_error': '',
        'finish_summary': '',
        'result': None,
    }


# --- the loop ------------------------------------------------------------------

def run_agent_loop(task, repo_path, settings=None):
    """Resolve `task` inside `repo_path`. Returns the result dict (see finalize)."""
    if not os.path.isdir(repo_path):
        raise ValueError('repository path does not exist: ' + repo_path)
    state = new_state(task, repo_path, build_settings(settings))
    phases = {
        'INIT': phase_init,
        'PLAN': phase_plan,
        'EXECUTE': phase_execute,
        'VERIFY': phase_verify,
        'RECOVER': phase_recover,
        'REVIEW': phase_review,
    }
    try:
        while state['phase'] not in TERMINAL_PHASES:
            reason = limits_exceeded(state)
            if reason:
                state['stop_reason'] = reason
                record_event(state, 'limit', reason)
                state['phase'] = 'FAILED'
                break
            log(state, 'phase ' + state['phase'])
            state['phase'] = phases[state['phase']](state)
    except Exception as error:
        state['fatal_error'] = type(error).__name__ + ': ' + str(error)
        record_event(state, 'crash', traceback.format_exc()[-3000:])
        state['phase'] = 'FAILED'
    finalize(state)
    return state['result']


def limits_exceeded(state):
    settings = state['settings']
    if state['step'] >= settings['max_steps']:
        return 'step limit reached ({0} model turns)'.format(settings['max_steps'])
    total = state['usage']['prompt_tokens'] + state['usage']['completion_tokens']
    if total >= settings['max_total_tokens']:
        return 'token limit reached ({0} tokens)'.format(total)
    elapsed = time.monotonic() - state['started_at']
    if elapsed >= settings['max_wall_seconds']:
        return 'wall-time limit reached ({0:.0f}s)'.format(elapsed)
    return ''


def log(state, message):
    if state['settings']['verbose']:
        elapsed = time.monotonic() - state['started_at']
        print('[OLIVER {0:6.1f}s] {1}'.format(elapsed, message), flush=True)


def record_event(state, kind, detail):
    state['trajectory'].append({
        'step': state['step'],
        'phase': state['phase'],
        'kind': kind,
        'detail': detail[:4000],
        'elapsed': round(time.monotonic() - state['started_at'], 2),
    })


# --- phases ------------------------------------------------------------------

def phase_init(state):
    settings = state['settings']
    repo = state['repo_path']
    state['model_ctx'] = foundation_model.configure_model(settings)
    index = context_manager.index_repository(repo)
    state['index'] = index
    state['hints'] = context_manager.extract_task_hints(state['task'])
    test_command = settings['test_command'] or verification.detect_test_command(repo, index)
    state['test_command'] = test_command
    tools.set_tool_context(repo, settings, index, test_command)

    baseline_summary = 'not run'
    if test_command and settings['run_baseline']:
        state['baseline'] = verification.run_tests(repo, test_command, [], settings)
        baseline_summary = verification.summarize_tests(state['baseline'])
    elif not test_command:
        baseline_summary = 'no test command detected'
    state['snapshot'] = recovery.init_snapshots(repo)

    repo_map = context_manager.build_repo_map(index, state['hints'], settings['repo_map_chars'])
    state['messages'] = [
        {'role': 'system', 'content': prompts.build_system_prompt(repo_map)},
        {'role': 'user', 'content': prompts.build_task_message(state['task'], test_command, baseline_summary,
                                                               state['hints'])},
    ]
    remember(state, 'fact', 'Test command: ' + (test_command if test_command else 'none') + '. Baseline: '
             + baseline_summary)
    record_event(state, 'init', 'indexed {0} files; backend {1}; test command: {2}; baseline: {3}'.format(
        len(index['files']), state['model_ctx']['backend'], test_command or 'none', baseline_summary))
    return 'PLAN'


def phase_plan(state):
    settings = state['settings']
    state['messages'].append({'role': 'user', 'content': prompts.plan_request(state['replan_note'])})
    state['replan_note'] = ''
    repaired = False
    for turn in range(settings['plan_steps'] + 1):
        if limits_exceeded(state):
            break
        last_turn = turn == settings['plan_steps']
        if last_turn:
            state['messages'].append({'role': 'user', 'content': prompts.PLAN_FINAL_CALL})
        outcome = run_model_turn(state, [] if last_turn else tools.PLAN_TOOLS, 'plan')
        if outcome['error']:
            if outcome['error'] == 'fatal':
                return 'FAILED'
            break
        if outcome['tool_calls']:
            continue
        plan = parse_plan(outcome['text'])
        if plan:
            state['plan'] = plan
            remember(state, 'decision', 'Plan: ' + plan['root_cause'])
            record_event(state, 'plan', json.dumps(plan))
            state['execute_prompted'] = False
            return 'EXECUTE'
        if repaired or last_turn:
            break
        repaired = True
        state['messages'].append({'role': 'user', 'content': prompts.PLAN_REPAIR})
    state['plan'] = fallback_plan(state)
    state['execute_prompted'] = False
    record_event(state, 'plan', 'fallback plan used: ' + json.dumps(state['plan']))
    return 'EXECUTE'


def phase_execute(state):
    settings = state['settings']
    if not state['execute_prompted']:
        state['messages'].append({'role': 'user', 'content': prompts.execute_request(state['plan'])})
        state['execute_prompted'] = True
    tools.reset_finish()
    nudges = 0
    for _ in range(settings['execute_steps']):
        if limits_exceeded(state):
            break
        outcome = run_model_turn(state, tools.ALL_TOOLS, 'execute')
        if outcome['error']:
            if outcome['error'] == 'fatal':
                return 'FAILED'
            break
        if outcome['finished']:
            state['finish_summary'] = tools.TOOL_CONTEXT['finish_summary']
            break
        if not outcome['tool_calls']:
            nudges += 1
            if nudges > 2:
                break
            state['messages'].append({'role': 'user', 'content': prompts.NUDGE})
            continue
        if recovery.detect_loop(state['action_history'], settings['loop_threshold']):
            state['loop_warnings'] += 1
            state['messages'].append({'role': 'user', 'content': prompts.loop_warning(settings['loop_threshold'])})
            record_event(state, 'loop', state['action_history'][-1])
            state['action_history'].append('(loop warning)')
            if state['loop_warnings'] >= 3:
                break
    return 'VERIFY'


def phase_verify(state):
    snapshot = state['snapshot']
    changed = recovery.changed_files(snapshot)
    result = verification.verify_changes(state['repo_path'], changed, state['baseline'], state['test_command'],
                                         state['plan'], state['settings'])
    state['verification'] = result
    sha = recovery.create_checkpoint(snapshot, 'verify-' + str(len(state['verifications']) + 1))
    state['verifications'].append({'checkpoint': sha, 'passed': result['passed'],
                                   'summary': verification.describe_verification(result)})
    record_event(state, 'verify', ('PASSED\n' if result['passed'] else 'FAILED\n')
                 + verification.describe_verification(result))
    if result['passed']:
        state['best_checkpoint'] = sha
        state['best_verification'] = result
        remember(state, 'fact', 'Verification passed at step ' + str(state['step']))
        if state['settings']['review'] and state['review_rounds'] < state['settings']['max_review_rounds']:
            return 'REVIEW'
        return 'DONE'
    return 'RECOVER'


def phase_recover(state):
    settings = state['settings']
    failure = recovery.classify_failure(state['verification'])
    state['failure_signatures'].append(failure['signature'])
    state['attempts'] += 1
    remember(state, 'failure', 'Attempt {0}: {1}. {2}'.format(
        state['attempts'], failure['headline'], failure['details'].splitlines()[0] if failure['details'] else ''))
    record_event(state, 'recover', failure['kind'] + ': ' + failure['signature'])
    if state['attempts'] > settings['max_recovery_attempts']:
        state['stop_reason'] = 'verification still failing after {0} recovery attempts'.format(
            settings['max_recovery_attempts'])
        return 'FAILED'
    if recovery.should_replan(state['failure_signatures']) and state['replans'] < settings['max_replans']:
        state['replans'] += 1
        target = state['best_checkpoint'] if state['best_checkpoint'] else state['snapshot']['baseline']
        recovery.restore_checkpoint(state['snapshot'], target)
        root_cause = state['plan']['root_cause'] if state['plan'] else 'unknown'
        remember(state, 'attempt', 'Rolled back an approach that failed twice (plan: ' + root_cause + ')')
        state['replan_note'] = prompts.replan_note(failure['signature'], context_manager.render_memory(state['memory']))
        record_event(state, 'replan', 'rolled back to ' + target[:10])
        return 'PLAN'
    state['messages'].append({'role': 'user', 'content': prompts.recovery_prompt(
        failure, state['attempts'], settings['max_recovery_attempts'], context_manager.render_memory(state['memory']))})
    return 'EXECUTE'


def phase_review(state):
    state['review_rounds'] += 1
    patch = recovery.export_patch(state['snapshot'])
    summary = verification.describe_verification(state['verification'])
    messages = [
        {'role': 'system', 'content': prompts.REVIEW_SYSTEM},
        {'role': 'user', 'content': prompts.review_request(state['task'], context_manager.truncate_output(patch, 20000),
                                                           summary)},
    ]
    response = foundation_model.call_model(state['model_ctx'], messages, [], state['settings'], 'review')
    if response['error']:
        record_event(state, 'review', 'review skipped: ' + response['error'])
        return 'DONE'
    record_usage(state, response, 'review')
    verdict = parse_review(response['text'])
    record_event(state, 'review', json.dumps(verdict))
    if verdict['verdict'] == 'revise' and verdict['issues']:
        remember(state, 'decision', 'Review asked for revisions: ' + '; '.join(verdict['issues'][:3]))
        state['messages'].append({'role': 'user', 'content': prompts.review_feedback(verdict['issues'])})
        return 'EXECUTE'
    return 'DONE'


# --- one model turn ------------------------------------------------------------

def run_model_turn(state, tool_names, purpose):
    fit_context(state, 1.0)
    specs = tools.tool_specs_for(tool_names)
    response = foundation_model.call_model(state['model_ctx'], state['messages'], specs, state['settings'], purpose)
    if response['error'] == 'context_overflow':
        record_event(state, 'overflow', 'context overflow; compressing harder and retrying')
        fit_context(state, 0.5)
        response = foundation_model.call_model(state['model_ctx'], state['messages'], specs, state['settings'],
                                               purpose)
    state['step'] += 1
    if response['error']:
        if response['error'] == 'fatal':
            state['fatal_error'] = response['error_message']
        record_event(state, 'model_error', response['error'] + ': ' + response['error_message'])
        return {'error': response['error'], 'tool_calls': 0, 'finished': False, 'text': ''}

    record_usage(state, response, purpose)
    state['messages'].append(assistant_message(response))
    record_event(state, 'model', '{0} tool call(s); finish_reason {1}; {2}'.format(
        len(response['tool_calls']), response['finish_reason'], response['text'][:300]))
    state['trajectory'][-1]['tokens'] = response['usage']['prompt_tokens'] + response['usage']['completion_tokens']

    finished = False
    for call in response['tool_calls']:
        output, ok = execute_tool_call(state, call, tool_names, response['finish_reason'])
        state['messages'].append({'role': 'tool', 'tool_call_id': call['id'], 'content': output})
        if call['name'] == 'finish' and ok:
            finished = True
    return {'error': '', 'tool_calls': len(response['tool_calls']), 'finished': finished, 'text': response['text']}


def assistant_message(response):
    message = {'role': 'assistant', 'content': response['text'] if response['text'] else None}
    if response['tool_calls']:
        message['tool_calls'] = [
            {'id': call['id'], 'type': 'function',
             'function': {'name': call['name'], 'arguments': call['raw_arguments']}}
            for call in response['tool_calls']
        ]
    elif message['content'] is None:
        message['content'] = '(no output)'
    return message


def execute_tool_call(state, call, allowed, finish_reason):
    """Every tool call gets exactly one result, even when it cannot run."""
    phase = state['phase']
    if call['error']:
        hint = ' The reply was cut off by the output limit; use smaller edits.' if finish_reason == 'length' else ''
        output, ok = 'ERROR: ' + call['error'] + '. Send the call again with a valid JSON object.' + hint, False
    elif call['name'] not in allowed:
        output, ok = prompts.tool_not_allowed(call['name'], phase, allowed), False
    else:
        result = tools.dispatch_tool_call(call['name'], call['arguments'])
        output, ok = result['output'], result['ok']
    output = context_manager.truncate_output(output, state['settings']['max_tool_output_chars'])
    state['action_history'].append(recovery.action_signature(call['name'], call['arguments']))
    state['trajectory'].append({
        'step': state['step'],
        'phase': phase,
        'kind': 'tool',
        'tool': call['name'],
        'target': tools.call_label(call['name'], call['arguments']),
        'ok': ok,
        'detail': output[:600],
        'elapsed': round(time.monotonic() - state['started_at'], 2),
    })
    return output, ok


def fit_context(state, factor):
    model_ctx = state['model_ctx']
    settings = state['settings']
    budget = int(model_ctx['context_window'] * settings['compress_ratio'] * factor)
    summary = 'Current plan:\n' + json.dumps(state['plan'], indent=2) + '\n\nWorking memory:\n' \
        + context_manager.render_memory(state['memory'])
    messages, stats = context_manager.compress_context(state['messages'], model_ctx, budget,
                                                       settings['keep_recent_messages'], 2, summary)
    if stats['compressed']:
        state['messages'] = messages
        record_event(state, 'compress', 'context {0} -> {1} tokens (masked {2}, dropped {3})'.format(
            stats['before'], stats['after'], stats['masked'], stats['dropped']))


def record_usage(state, response, purpose):
    usage = state['usage']
    prompt_tokens = response['usage']['prompt_tokens']
    completion_tokens = response['usage']['completion_tokens']
    usage['prompt_tokens'] += prompt_tokens
    usage['completion_tokens'] += completion_tokens
    usage['cached_tokens'] += response['usage']['cached_tokens']
    usage['cost'] += response['cost']
    if purpose not in usage['by_phase']:
        usage['by_phase'][purpose] = {'calls': 0, 'prompt_tokens': 0, 'completion_tokens': 0, 'cost': 0.0}
    phase_usage = usage['by_phase'][purpose]
    phase_usage['calls'] += 1
    phase_usage['prompt_tokens'] += prompt_tokens
    phase_usage['completion_tokens'] += completion_tokens
    phase_usage['cost'] += response['cost']
    state['model_calls'] += 1


def remember(state, kind, text):
    context_manager.append_memory(state['memory'], kind, text, state['step'])


# --- parsing helpers -----------------------------------------------------------

def extract_json_object(text):
    """Find the first JSON object in a reply (fenced or bare)."""
    candidates = re.findall(r'```(?:json)?\s*(\{.*?\})\s*```', text, re.DOTALL)
    start = text.find('{')
    end = text.rfind('}')
    if start != -1 and end > start:
        candidates.append(text[start:end + 1])
    for candidate in candidates:
        try:
            value = json.loads(candidate)
        except ValueError:
            continue
        if isinstance(value, dict):
            return value
    return None


def as_string_list(value):
    if isinstance(value, list):
        return [str(item) for item in value if str(item).strip()]
    if isinstance(value, str) and value.strip():
        return [value]
    return []


def parse_plan(text):
    data = extract_json_object(text)
    if not data or 'root_cause' not in data or 'steps' not in data:
        return None
    return {
        'root_cause': str(data['root_cause']),
        'files_to_change': as_string_list(data['files_to_change'] if 'files_to_change' in data else []),
        'steps': as_string_list(data['steps'])[:12],
        'reproduction_command': safe_reproduction(data),
        'tests_to_run': as_string_list(data['tests_to_run'] if 'tests_to_run' in data else []),
    }


def safe_reproduction(data):
    """The plan's reproduction command runs during verification, so it gets the
    same blocklist as run_command; a refused command is dropped."""
    if 'reproduction_command' not in data or not data['reproduction_command']:
        return ''
    command = str(data['reproduction_command']).strip()
    return '' if verification.blocked_reason(command) else command


def fallback_plan(state):
    ranked = context_manager.rank_files(state['index'], state['hints'])
    likely = [rel for rel, score in ranked[:3] if score > 0]
    return {
        'root_cause': 'not identified during planning',
        'files_to_change': likely,
        'steps': ['Locate the code responsible for the task', 'Reproduce the problem', 'Fix the root cause',
                  'Run the relevant tests', 'Call finish'],
        'reproduction_command': '',
        'tests_to_run': [],
    }


def parse_review(text):
    data = extract_json_object(text)
    if not data or 'verdict' not in data:
        return {'verdict': 'approve', 'issues': []}
    verdict = str(data['verdict']).strip().lower()
    issues = as_string_list(data['issues'] if 'issues' in data else [])
    return {'verdict': 'revise' if verdict == 'revise' else 'approve', 'issues': issues[:6]}


# --- finalize ------------------------------------------------------------------

def finalize(state):
    """Settle on the best verified state, export the patch and build the result."""
    snapshot = state['snapshot']
    verified = state['phase'] == 'DONE'
    note = ''
    patch = ''
    changed = []
    if snapshot:
        try:
            if not verified and state['best_checkpoint']:
                recovery.restore_checkpoint(snapshot, state['best_checkpoint'])
                state['verification'] = state['best_verification']
                verified = True
                note = 'Restored the last verified checkpoint after: ' + (state['stop_reason'] or 'failure')
            patch = recovery.export_patch(snapshot)
            changed = recovery.changed_files(snapshot)
        finally:
            recovery.cleanup_snapshots(snapshot)
    if verified:
        status = 'resolved'
    elif patch.strip():
        status = 'unverified'
    else:
        status = 'failed'
    usage = state['usage']
    state['result'] = {
        'status': status,
        'task': state['task'],
        'repo': state['repo_path'],
        'model': state['settings']['model'],
        'backend': state['model_ctx']['backend'] if state['model_ctx'] else '',
        'final_phase': state['phase'],
        'stop_reason': state['stop_reason'],
        'fatal_error': state['fatal_error'],
        'note': note,
        'patch': patch,
        'changed_files': changed,
        'plan': state['plan'],
        'finish_summary': state['finish_summary'],
        'test_command': state['test_command'],
        'baseline': state['baseline'],
        'verification': state['verification'],
        'verifications': state['verifications'],
        'memory': state['memory'],
        'trajectory': state['trajectory'],
        'usage': {
            'prompt_tokens': usage['prompt_tokens'],
            'completion_tokens': usage['completion_tokens'],
            'total_tokens': usage['prompt_tokens'] + usage['completion_tokens'],
            'cached_tokens': usage['cached_tokens'],
            'cost': round(usage['cost'], 6),
            'by_phase': usage['by_phase'],
        },
        'steps': state['step'],
        'model_calls': state['model_calls'],
        'attempts': state['attempts'],
        'replans': state['replans'],
        'duration_seconds': round(time.monotonic() - state['started_at'], 2),
    }
