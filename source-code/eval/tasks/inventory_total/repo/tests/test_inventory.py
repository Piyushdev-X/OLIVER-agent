from inventory import order_total


def test_single_items():
    assert order_total([{'price': 2, 'quantity': 1}, {'price': 3, 'quantity': 1}]) == 5


def test_empty_order():
    assert order_total([]) == 0
