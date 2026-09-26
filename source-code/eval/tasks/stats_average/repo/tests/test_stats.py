from stats import average, total


def test_total():
    assert total([1, 2, 3]) == 6


def test_average_of_empty_list_is_zero():
    assert average([]) == 0.0
