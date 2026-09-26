"""Text statistics."""


def word_count(text):
    if not text:
        return 0
    return len(text.split(' '))
