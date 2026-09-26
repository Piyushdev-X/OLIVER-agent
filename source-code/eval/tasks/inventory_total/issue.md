# order_total ignores quantities

`inventory.order_total([{"price": 2, "quantity": 3}])` returns `2`; it should return `6`.
Each line item must contribute price times quantity.
