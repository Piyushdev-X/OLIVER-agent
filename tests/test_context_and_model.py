import json

import litellm

from oliver import context_manager
from oliver import foundation_model
from oliver import orchestrator
from support import make_repo

PY_SOURCE = ('import os\n\n\ndef helper():\n    return 1\n\n\n'
             'class Parser:\n    def parse(parser_obj, text):\n        return text\n')


def test_index_extracts_python_symbols_with_tree_sitter_and_ast(tmp_path):
    repo = make_repo(tmp_path, {'src/parser.py': PY_SOURCE, 'web/app.js': 'export function render() {}\n'})
    index = context_manager.index_repository(repo)
    names = [symbol['name'] for symbol in index['symbols']['src/parser.py']]
    assert names == ['helper', 'Parser', 'Parser.parse']
    assert [s['name'] for s in context_manager.python_symbols_ast(PY_SOURCE)] == names
    assert index['symbols']['web/app.js'][0]['name'] == 'render'


def test_hints_and_ranking_prefer_mentioned_files(tmp_path):
    repo = make_repo(tmp_path, {'a/other.py': 'x = 1\n', 'b/parser.py': PY_SOURCE})
    index = context_manager.index_repository(repo)
    hints = context_manager.extract_task_hints('File "b/parser.py", line 3: parse_text fails with KeyError')
    assert 'b/parser.py' in hints['paths'] and 'KeyError' in hints['errors']
    assert context_manager.rank_files(index, hints)[0][0] == 'b/parser.py'
    assert 'b/parser.py' in context_manager.build_repo_map(index, hints, 4000)


def test_compression_keeps_tool_calls_paired():
    model_ctx = {'backend': 'mock', 'model': 'mock'}
    messages = [{'role': 'system', 'content': 'sys'}, {'role': 'user', 'content': 'task'}]
    for number in range(30):
        call_id = 'c' + str(number)
        messages.append({'role': 'assistant', 'content': None, 'tool_calls': [
            {'id': call_id, 'type': 'function', 'function': {'name': 'read_file', 'arguments': '{}'}}]})
        messages.append({'role': 'tool', 'tool_call_id': call_id, 'content': 'x' * 2000})
    compressed, stats = context_manager.compress_context(messages, model_ctx, 3000, 6, 2, 'plan + memory')
    assert stats['compressed'] and stats['after'] < stats['before']
    assert compressed[0]['content'] == 'sys' and compressed[1]['content'] == 'task'
    seen = set()
    for message in compressed:
        if message['role'] == 'assistant' and 'tool_calls' in message:
            seen.update(call['id'] for call in message['tool_calls'])
        if message['role'] == 'tool':
            assert message['tool_call_id'] in seen


def test_memory_deduplicates_and_output_truncates():
    memory = []
    context_manager.append_memory(memory, 'fact', 'same', 1)
    context_manager.append_memory(memory, 'fact', 'same', 2)
    assert len(memory) == 1
    text = context_manager.truncate_output('a' * 1000, 100)
    assert 'characters omitted' in text and len(text) < 200


def test_tool_call_parsing_and_text_protocol():
    bad = foundation_model.parse_tool_call('id1', 'read_file', '{not json')
    assert bad['arguments'] is None and 'invalid JSON' in bad['error']
    text, calls = foundation_model.extract_text_tool_calls(
        'Let me look.\n<tool_call>{"name": "read_file", "arguments": {"filepath": "a.py"}}</tool_call>')
    assert text == 'Let me look.' and calls[0]['arguments'] == {'filepath': 'a.py'}
    messages = [
        {'role': 'system', 'content': 'sys'},
        {'role': 'assistant', 'content': None, 'tool_calls': [
            {'id': 'c1', 'type': 'function', 'function': {'name': 'read_file', 'arguments': '{"filepath": "a"}'}}]},
        {'role': 'tool', 'tool_call_id': 'c1', 'content': 'body'},
        {'role': 'user', 'content': 'next'},
    ]
    converted = foundation_model.to_text_protocol(messages, [])
    assert [m['role'] for m in converted] == ['system', 'assistant', 'user']
    assert 'TOOL RESULT from read_file' in converted[2]['content']


def test_cache_hints_only_for_anthropic_models():
    messages = [{'role': 'system', 'content': 'stable prefix'}, {'role': 'user', 'content': 'q'}]
    hinted = foundation_model.add_cache_hints(messages, 'anthropic/claude-sonnet-4-5')
    assert hinted[0]['content'][0]['cache_control'] == {'type': 'ephemeral'}
    assert foundation_model.add_cache_hints(messages, 'openai/gpt-4o') is messages


def test_litellm_backend_uses_alias_retries_and_detects_overflow(monkeypatch):
    settings = orchestrator.build_settings({'model': 'gpt-4o', 'retry_base_seconds': 0.0})
    model_ctx = foundation_model.configure_model(settings)
    assert model_ctx['backend'] == 'native'
    assert litellm.model_alias_map['OLIVER'] == 'gpt-4o'
    real_completion = litellm.completion
    calls = {'count': 0}

    def flaky_completion(**kwargs):
        calls['count'] += 1
        assert kwargs['model'] == 'OLIVER'
        if calls['count'] < 3:
            raise litellm.exceptions.RateLimitError('slow down', llm_provider='openai', model='gpt-4o')
        return real_completion(model='gpt-4o', messages=kwargs['messages'], mock_tool_calls=[
            {'id': 'call_1', 'type': 'function', 'function': {'name': 'finish', 'arguments': json.dumps({'summary': 's'})}}])

    monkeypatch.setattr(litellm, 'completion', flaky_completion)
    response = foundation_model.call_model(model_ctx, [{'role': 'user', 'content': 'hi'}], [], settings, 'execute')
    assert calls['count'] == 3 and response['tool_calls'][0]['name'] == 'finish'

    def overflowing_completion(**kwargs):
        raise litellm.exceptions.ContextWindowExceededError('too long', model='gpt-4o', llm_provider='openai')

    monkeypatch.setattr(litellm, 'completion', overflowing_completion)
    response = foundation_model.call_model(model_ctx, [{'role': 'user', 'content': 'hi'}], [], settings, 'execute')
    assert response['error'] == 'context_overflow'
