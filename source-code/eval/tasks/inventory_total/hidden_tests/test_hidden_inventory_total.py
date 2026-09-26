from inventory import order_total


def test_quantities_multiply_price():
    assert order_total([{'price': 2, 'quantity': 3}, {'price': 5, 'quantity': 1}]) == 11


def test_zero_quantity():
    assert order_total([{'price': 9, 'quantity': 0}]) == 0
