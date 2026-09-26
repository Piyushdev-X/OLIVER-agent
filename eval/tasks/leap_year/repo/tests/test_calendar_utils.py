from calendar_utils import days_in_year, is_leap_year


def test_ordinary_years():
    assert is_leap_year(2024)
    assert not is_leap_year(2023)


def test_days_in_year():
    assert days_in_year(2023) == 365
