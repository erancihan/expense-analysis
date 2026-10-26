"""Calendar-month helpers. Months are 'YYYY-MM' strings throughout the package."""

import calendar
from datetime import date
from typing import List


def month_key(d: date) -> str:
    """'YYYY-MM' for a date or datetime."""
    return f'{d.year:04d}-{d.month:02d}'


def add_months(d: date, n: int) -> date:
    """`d` moved by `n` calendar months, the day clamped to the month's length."""
    index = d.year * 12 + (d.month - 1) + n
    year, month = divmod(index, 12)
    month += 1
    day = min(d.day, calendar.monthrange(year, month)[1])
    return d.replace(year=year, month=month, day=day)


def month_range(first: str, last: str) -> List[str]:
    """Every month from `first` to `last`, inclusive; empty if first > last."""
    year, month = (int(part) for part in first.split('-'))
    months = []
    while f'{year:04d}-{month:02d}' <= last:
        months.append(f'{year:04d}-{month:02d}')
        month += 1
        if month == 13:
            year, month = year + 1, 1
    return months
