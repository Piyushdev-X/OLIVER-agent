from config import merge_config


def test_flat_override():
    assert merge_config({'a': 1, 'b': 2}, {'b': 3}) == {'a': 1, 'b': 3}
