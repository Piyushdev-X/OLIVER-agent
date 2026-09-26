from calendar_utils import days_in_year, is_leap_year


def test_century_years():
    assert not is_leap_year(1900)
    assert not is_leap_year(2100)
    assert is_leap_year(2000)


def test_days_in_century_year():
    assert days_in_year(1900) == 365
    assert days_in_year(2000) == 366
