from text_stats import word_count


def test_repeated_spaces_and_newlines():
    assert word_count('a  b\nc') == 3


def test_only_whitespace():
    assert word_count('   ') == 0
