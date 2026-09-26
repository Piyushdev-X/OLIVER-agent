# average() returns the wrong value

Calling `stats.average([2, 4])` returns `2.0`, but the average of 2 and 4 is `3.0`.
The empty-list case (`average([])` returns `0.0`) is correct and must keep working.
