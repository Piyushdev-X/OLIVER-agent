"""Text helpers."""


def slugify(title):
    words = title.split(' ')
    return '-'.join(words)
