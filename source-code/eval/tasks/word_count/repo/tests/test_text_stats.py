from text_stats import word_count


def test_simple_sentence():
    assert word_count('one two three') == 3


def test_empty():
    assert word_count('') == 0
