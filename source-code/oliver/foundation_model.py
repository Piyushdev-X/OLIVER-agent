"""Foundation model access for OLIVER.

Every model call in the harness goes through call_model(). The rest of the code
refers to the model only by the alias "OLIVER"; configure_model() maps that alias
to the real LiteLLM model id from settings['model'] (configuration-files/oliver.toml).
Responses are converted to plain dicts right here, so the rest of the harness
only ever uses key access.

The credential comes from the AI_API_KEY environment variable, read at call
time by api_key(). It never enters settings, run artifacts or logs, and
redact_secret() strips it from any error text before that text is kept.

Backends:
  native  LiteLLM with OpenAI-format tool calling
  text    LiteLLM for models without tool support: tools are described in the
          system prompt and parsed back from <tool_call>{...}</tool_call> blocks
  mock    scripted replies from a JSON file, for tests and offline demos
"""

import json
import os
import random
import re
import time

MODEL_ALIAS = 'OLIVER'
API_KEY_VARIABLE = 'AI_API_KEY'
REDACTED = '[' + API_KEY_VARIABLE + ']'

TOOL_BLOCK = re.compile(r'<tool_call>\s*(\{.*?\})\s*</tool_call>', re.DOTALL)
FENCED_JSON = re.compile(r'```(?:json)?\s*(\{.*?\})\s*```', re.DOTALL)
OVERFLOW_HINTS = ['context length', 'context window', 'maximum context', 'too many tokens',
                  'prompt is too long', 'input is too long']

CALL_COUNTER = {'value': 0}


# --- configuration -----------------------------------------------------------

def api_key():
    """The evaluation credential, read from the environment on every call."""
    if API_KEY_VARIABLE in os.environ:
        return os.environ[API_KEY_VARIABLE].strip()
    return ''


def redact_secret(text):
    """Replace the credential wherever it appears in text that may be shown or saved."""
    key = api_key()
    if len(key) >= 8 and key in text:
        return text.replace(key, REDACTED)
    return text


def is_mock_model(model_name):
    return model_name == 'mock' or model_name.startswith('mock:')


def configure_model(settings):
    """Prepare the model context dict used by every later call."""
    model_name = settings['model']
    context = {
        'alias': MODEL_ALIAS,
        'model': model_name,
        'backend': 'native',
        'mock': None,
        'context_window': settings['context_tokens'],
    }
    if is_mock_model(model_name):
        context['backend'] = 'mock'
        context['mock'] = load_mock_script(model_name)
        return context

    import litellm
    litellm.suppress_debug_info = True
    litellm.drop_params = True
    litellm.model_alias_map[MODEL_ALIAS] = model_name

    mode = settings['tool_mode']
    if mode == 'text' or (mode == 'auto' and not model_supports_tools(model_name)):
        context['backend'] = 'text'
    window = model_context_window(model_name)
    if window:
        context['context_window'] = min(settings['context_tokens'], int(window * 0.8))
    return context


def model_supports_tools(model_name):
    import litellm
    try:
        return bool(litellm.supports_function_calling(model=model_name))
    except Exception:
        return False


def model_context_window(model_name):
    from litellm import get_model_info
    try:
        info = get_model_info(model_name)
    except Exception:
        return 0
    if 'max_input_tokens' in info and info['max_input_tokens']:
        return int(info['max_input_tokens'])
    return 0


# --- calls -------------------------------------------------------------------

def call_model(model_ctx, messages, tool_specs, settings, purpose):
    """Call the model once. Always returns a normalized response dict:
    {text, tool_calls, finish_reason, usage, cost, error, error_message}."""
    if model_ctx['backend'] == 'mock':
        return call_mock_model(model_ctx, messages, purpose)
    return call_litellm(model_ctx, messages, tool_specs, settings)


def call_litellm(model_ctx, messages, tool_specs, settings):
    import litellm
    from litellm import completion

    text_mode = model_ctx['backend'] == 'text'
    request_messages = messages
    if text_mode:
        request_messages = to_text_protocol(messages, tool_specs)
    request_messages = add_cache_hints(request_messages, model_ctx['model'])

    kwargs = request_arguments(settings)
    kwargs['messages'] = request_messages
    kwargs['max_tokens'] = settings['max_output_tokens']
    kwargs['temperature'] = settings['temperature']
    if settings['seed'] >= 0:
        kwargs['seed'] = settings['seed']
    if tool_specs and not text_mode:
        kwargs['tools'] = tool_specs
        kwargs['tool_choice'] = 'auto'

    errors = litellm.exceptions
    attempt = 0
    while True:
        try:
            response = completion(**kwargs)
        except errors.ContextWindowExceededError as error:
            return error_response('context_overflow', str(error))
        except (errors.AuthenticationError, errors.NotFoundError, errors.PermissionDeniedError) as error:
            return error_response('fatal', str(error))
        except errors.BadRequestError as error:
            message = str(error)
            if looks_like_overflow(message):
                return error_response('context_overflow', message)
            return error_response('bad_request', message)
        except (errors.RateLimitError, errors.APIConnectionError, errors.ServiceUnavailableError,
                errors.InternalServerError, errors.APIError) as error:
            if attempt >= settings['max_retries']:
                return error_response('model_unavailable', str(error))
            time.sleep(backoff_seconds(attempt, settings['retry_base_seconds']))
            attempt += 1
            continue
        return normalize_response(response, text_mode)


def request_arguments(settings):
    """Arguments shared by every LiteLLM request: the alias, the credential
    from AI_API_KEY and the optional gateway."""
    kwargs = {'model': MODEL_ALIAS, 'timeout': settings['request_timeout']}
    key = api_key()
    if key:
        kwargs['api_key'] = key
    if settings['api_base']:
        kwargs['api_base'] = settings['api_base']
    return kwargs


def check_model(settings):
    """Send one tiny request to prove the model id and AI_API_KEY work together.
    Returns (kind, message): kind is '' on success, 'fatal' when the provider
    rejects the credential or the model, and 'warning' for anything else."""
    model_ctx = configure_model(settings)
    if model_ctx['backend'] == 'mock':
        return '', ''
    import litellm
    from litellm import completion

    errors = litellm.exceptions
    kwargs = request_arguments(settings)
    kwargs['messages'] = [{'role': 'user', 'content': 'Reply with the word OK.'}]
    kwargs['max_tokens'] = 64
    kwargs['timeout'] = min(60, settings['request_timeout'])
    try:
        completion(**kwargs)
    except (errors.AuthenticationError, errors.PermissionDeniedError, errors.NotFoundError) as error:
        return 'fatal', redact_secret(type(error).__name__ + ': ' + str(error))[:600]
    except Exception as error:
        return 'warning', redact_secret(type(error).__name__ + ': ' + str(error))[:600]
    return '', ''


def backoff_seconds(attempt, base):
    return min(60.0, base * (2 ** attempt)) + random.uniform(0, base / 2.0)


def looks_like_overflow(message):
    lowered = message.lower()
    for hint in OVERFLOW_HINTS:
        if hint in lowered:
            return True
    return False


def error_response(kind, message):
    return {
        'text': '',
        'tool_calls': [],
        'finish_reason': 'error',
        'usage': {'prompt_tokens': 0, 'completion_tokens': 0, 'cached_tokens': 0},
        'cost': 0.0,
        'error': kind,
        'error_message': redact_secret(message)[:2000],
    }


def normalize_response(response, text_mode):
    data = response.model_dump()
    choice = data['choices'][0]
    message = choice['message']
    text = message['content'] if 'content' in message and message['content'] else ''
    calls = []
    raw_calls = message['tool_calls'] if 'tool_calls' in message and message['tool_calls'] else []
    for raw in raw_calls:
        function = raw['function']
        calls.append(parse_tool_call(raw['id'], function['name'], function['arguments']))
    if text_mode:
        text, calls = extract_text_tool_calls(text)

    usage = data['usage'] if 'usage' in data and data['usage'] else {}
    cached = 0
    if 'prompt_tokens_details' in usage and usage['prompt_tokens_details']:
        details = usage['prompt_tokens_details']
        if 'cached_tokens' in details and details['cached_tokens']:
            cached = details['cached_tokens']
    return {
        'text': text,
        'tool_calls': calls,
        'finish_reason': choice['finish_reason'] if choice['finish_reason'] else 'stop',
        'usage': {
            'prompt_tokens': usage['prompt_tokens'] if 'prompt_tokens' in usage and usage['prompt_tokens'] else 0,
            'completion_tokens': usage['completion_tokens'] if 'completion_tokens' in usage and usage['completion_tokens'] else 0,
            'cached_tokens': cached,
        },
        'cost': response_cost(response),
        'error': '',
        'error_message': '',
    }


def response_cost(response):
    from litellm import completion_cost
    try:
        return float(completion_cost(completion_response=response))
    except Exception:
        return 0.0


def new_call_id():
    CALL_COUNTER['value'] += 1
    return 'call_oliver_' + str(CALL_COUNTER['value'])


def parse_tool_call(call_id, name, raw_arguments):
    """Tool arguments are always parsed as JSON, never string-matched."""
    error = ''
    if isinstance(raw_arguments, dict):
        arguments = raw_arguments
        raw_text = json.dumps(raw_arguments)
    else:
        raw_text = raw_arguments if raw_arguments else '{}'
        try:
            arguments = json.loads(raw_text)
        except ValueError as exc:
            arguments = None
            error = 'invalid JSON arguments (' + str(exc) + ')'
        if arguments is not None and not isinstance(arguments, dict):
            arguments = None
            error = 'arguments must be a JSON object'
    return {
        'id': call_id if call_id else new_call_id(),
        'name': name if name else '',
        'arguments': arguments,
        'raw_arguments': raw_text,
        'error': error,
    }


# --- prompt caching ----------------------------------------------------------

def is_anthropic_model(model_name):
    lowered = model_name.lower()
    return lowered.startswith('anthropic/') or 'claude' in lowered


def add_cache_hints(messages, model_name):
    """Mark the stable system prefix as cacheable for Anthropic models.
    Other providers cache long stable prefixes automatically."""
    if not is_anthropic_model(model_name) or not messages:
        return messages
    first = messages[0]
    if first['role'] != 'system' or not isinstance(first['content'], str):
        return messages
    cached_system = {
        'role': 'system',
        'content': [{'type': 'text', 'text': first['content'], 'cache_control': {'type': 'ephemeral'}}],
    }
    return [cached_system] + messages[1:]


# --- text protocol (models without native tool calling) ----------------------

def to_text_protocol(messages, tool_specs):
    """Rewrite OpenAI-format tool traffic as plain text turns."""
    from oliver import prompts
    converted = []
    names_by_id = {}
    for message in messages:
        role = message['role']
        content = message['content'] if 'content' in message and message['content'] else ''
        if role == 'system' and not converted:
            if tool_specs:
                content = content + '\n\n' + prompts.render_tool_catalog(tool_specs)
            converted.append({'role': 'system', 'content': content})
            continue
        if role == 'assistant':
            blocks = []
            if 'tool_calls' in message and message['tool_calls']:
                for call in message['tool_calls']:
                    function = call['function']
                    names_by_id[call['id']] = function['name']
                    try:
                        arguments = json.loads(function['arguments'])
                    except ValueError:
                        arguments = {}
                    blocks.append('<tool_call>' + json.dumps({'name': function['name'], 'arguments': arguments}) + '</tool_call>')
            text = (content + '\n' + '\n'.join(blocks)).strip()
            append_merged(converted, 'assistant', text if text else '(no output)')
            continue
        if role == 'tool':
            call_id = message['tool_call_id']
            name = names_by_id[call_id] if call_id in names_by_id else 'tool'
            append_merged(converted, 'user', 'TOOL RESULT from ' + name + ':\n' + content)
            continue
        append_merged(converted, role, content)
    return converted


def append_merged(converted, role, content):
    """Merge consecutive same-role turns; some providers require alternation."""
    if converted and converted[-1]['role'] == role and role != 'system':
        converted[-1] = {'role': role, 'content': converted[-1]['content'] + '\n\n' + content}
    else:
        converted.append({'role': role, 'content': content})


def extract_text_tool_calls(text):
    calls = []
    for match in TOOL_BLOCK.finditer(text):
        calls.append(call_from_json_text(match.group(1)))
    remaining = TOOL_BLOCK.sub('', text).strip()
    if not calls:
        for match in FENCED_JSON.finditer(text):
            candidate = match.group(1)
            if '"name"' in candidate and '"arguments"' in candidate:
                calls.append(call_from_json_text(candidate))
        if calls:
            remaining = FENCED_JSON.sub('', text).strip()
    return remaining, calls


def call_from_json_text(block):
    try:
        payload = json.loads(block)
    except ValueError as exc:
        return {'id': new_call_id(), 'name': 'unknown', 'arguments': None, 'raw_arguments': block,
                'error': 'invalid JSON in tool_call block (' + str(exc) + ')'}
    if not isinstance(payload, dict):
        return {'id': new_call_id(), 'name': 'unknown', 'arguments': None, 'raw_arguments': block,
                'error': 'tool_call block must hold a JSON object'}
    name = payload['name'] if 'name' in payload else ''
    arguments = payload['arguments'] if 'arguments' in payload else {}
    if not isinstance(arguments, (dict, str)):
        arguments = json.dumps(arguments)
    return parse_tool_call('', name, arguments)


# --- token counting ----------------------------------------------------------

def approximate_tokens(messages):
    return max(1, len(json.dumps(messages, default=str)) // 4)


def count_tokens(model_ctx, messages):
    if model_ctx['backend'] == 'mock':
        return approximate_tokens(messages)
    from litellm import token_counter
    try:
        return int(token_counter(model=model_ctx['model'], messages=messages))
    except Exception:
        return approximate_tokens(messages)


# --- mock backend ------------------------------------------------------------

def load_mock_script(model_name):
    """mock or mock:/path/script.json. A script maps a purpose (plan, execute,
    review) to a list of turns: {"text": ..., "tool_calls": [{"name", "arguments"}]}
    or {"error": "context_overflow"} to simulate an API failure."""
    path = model_name.split(':', 1)[1] if ':' in model_name else ''
    queues = {}
    if path:
        with open(path, 'r', encoding='utf-8') as handle:
            data = json.load(handle)
        for purpose in data:
            queues[purpose] = list(data[purpose])
    return {'path': path, 'queues': queues, 'positions': {}}


def default_mock_turn(purpose):
    if purpose == 'plan':
        plan = {'root_cause': 'unknown (mock model)', 'files_to_change': [], 'steps': ['Inspect and fix the issue'],
                'reproduction_command': '', 'tests_to_run': []}
        return {'text': '```json\n' + json.dumps(plan) + '\n```'}
    if purpose == 'execute':
        return {'tool_calls': [{'name': 'finish', 'arguments': {'summary': 'Mock script finished.'}}]}
    if purpose == 'review':
        return {'text': '{"verdict": "approve", "issues": []}'}
    return {'text': ''}


def call_mock_model(model_ctx, messages, purpose):
    mock = model_ctx['mock']
    queue = mock['queues'][purpose] if purpose in mock['queues'] else []
    position = mock['positions'][purpose] if purpose in mock['positions'] else 0
    if position < len(queue):
        turn = queue[position]
        mock['positions'][purpose] = position + 1
    else:
        turn = default_mock_turn(purpose)
    if 'error' in turn:
        return error_response(turn['error'], 'simulated by mock script')
    text = turn['text'] if 'text' in turn else ''
    turn_calls = turn['tool_calls'] if 'tool_calls' in turn else []
    calls = []
    for index, call in enumerate(turn_calls):
        arguments = call['arguments'] if 'arguments' in call else {}
        raw = arguments if isinstance(arguments, str) else json.dumps(arguments)
        call_id = 'mock_' + purpose + '_' + str(position) + '_' + str(index)
        calls.append(parse_tool_call(call_id, call['name'], raw))
    completion_tokens = approximate_tokens([{'text': text, 'calls': turn_calls}])
    return {
        'text': text,
        'tool_calls': calls,
        'finish_reason': 'tool_calls' if calls else 'stop',
        'usage': {'prompt_tokens': approximate_tokens(messages), 'completion_tokens': completion_tokens,
                  'cached_tokens': 0},
        'cost': 0.0,
        'error': '',
        'error_message': '',
    }
