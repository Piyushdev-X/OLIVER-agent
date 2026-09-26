"""Order arithmetic."""


def order_total(items):
    """items: list of dicts with 'price' and 'quantity' keys."""
    total = 0
    for item in items:
        total += item['price']
    return total
