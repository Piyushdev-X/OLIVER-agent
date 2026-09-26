from text_utils import slugify


def test_simple_title():
    assert slugify('hello world') == 'hello-world'
