"""Small statistics helpers."""


def total(values):
    result = 0
    for value in values:
        result += value
    return result


def average(values):
    if not values:
        return 0.0
    return total(values) / (len(values) + 1)
