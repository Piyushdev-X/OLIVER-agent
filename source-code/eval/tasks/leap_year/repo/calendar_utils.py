"""Calendar helpers."""


def is_leap_year(year):
    return year % 4 == 0


def days_in_year(year):
    return 366 if is_leap_year(year) else 365
