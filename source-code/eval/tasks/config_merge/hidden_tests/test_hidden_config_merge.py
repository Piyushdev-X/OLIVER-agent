from config import merge_config


def test_nested_dicts_are_merged():
    base = {'db': {'host': 'localhost', 'port': 5432}, 'debug': False}
    result = merge_config(base, {'db': {'port': 6543}})
    assert result == {'db': {'host': 'localhost', 'port': 6543}, 'debug': False}


def test_base_is_not_modified():
    base = {'db': {'port': 1}}
    merge_config(base, {'db': {'port': 2}})
    assert base == {'db': {'port': 1}}
