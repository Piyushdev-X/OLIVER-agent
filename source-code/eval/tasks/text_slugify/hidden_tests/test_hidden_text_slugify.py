from text_utils import slugify


def test_mixed_case_and_punctuation():
    assert slugify('Hello, World!') == 'hello-world'


def test_extra_whitespace():
    assert slugify('  Ready   Set  Go ') == 'ready-set-go'


def test_digits_are_kept():
    assert slugify('Top 10 Tips') == 'top-10-tips'
