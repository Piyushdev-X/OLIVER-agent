from stats import average


def test_average_of_two_values():
    assert average([2, 4]) == 3.0


def test_average_of_one_value():
    assert average([5]) == 5.0


def test_average_of_empty_list_is_zero():
    assert average([]) == 0.0
