# 1900 is reported as a leap year

`calendar_utils.is_leap_year(1900)` returns `True`. Century years are leap years only when
divisible by 400, so 1900 and 2100 are not leap years while 2000 is.
