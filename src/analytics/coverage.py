"""Which calendar months the imported statements fully cover.

Each statement covers the month before its closing date: (closing − 1 month,
closing]. A card is active from one month before its first closing date to its
last. A missing statement leaves part of a card's active span outside every
window; a gap between one card's last statement and the next card's first
leaves days that no card covers. Either way the month's spend is understated,
so charts must not draw it as a real low.
"""

from collections import defaultdict
from datetime import date, timedelta
from typing import Dict, List, Set

from sqlalchemy import func
from sqlalchemy.orm import Session

from src.analytics.months import add_months, month_key, month_range
from src.models import Expense, Statement

COMPLETE = 'complete'
INCOMPLETE = 'incomplete'
NONE = 'none'

# Closing dates drift by a day or two between cycles (28th vs 30th), leaving
# slivers outside every window. A missing statement leaves about a month.
MIN_UNCOVERED_DAYS = 7


def _days(after: date, through: date) -> Set[date]:
    """Every day in (after, through]."""
    days = set()
    day = after + timedelta(days=1)
    while day <= through:
        days.add(day)
        day += timedelta(days=1)
    return days


def compute_coverage(
    closings_by_card: Dict[str, List[date]], first_month: str, last_month: str,
) -> Dict[str, str]:
    """Map each month in [first_month, last_month] to complete/incomplete/none.

    none: no statement covers any day of the month.
    incomplete: some card has MIN_UNCOVERED_DAYS or more active-but-uncovered
        days in the month, or that many days inside the overall span are
        covered by no card at all (a gap between cards).
    complete: otherwise.
    """
    cards = []
    covered_any: Set[date] = set()
    span_start = span_end = None
    for closings in closings_by_card.values():
        closings = sorted(set(closings))
        if not closings:
            continue
        start = add_months(closings[0], -1)
        active = _days(start, closings[-1])
        covered: Set[date] = set()
        for closing in closings:
            covered |= _days(add_months(closing, -1), closing)
        cards.append((active, covered))
        covered_any |= covered
        span_start = start if span_start is None else min(span_start, start)
        span_end = closings[-1] if span_end is None else max(span_end, closings[-1])
    span = _days(span_start, span_end) if cards else set()

    result = {}
    for month in month_range(first_month, last_month):
        first_day = date(int(month[:4]), int(month[5:]), 1)
        days = _days(first_day - timedelta(days=1), add_months(first_day, 1) - timedelta(days=1))
        if not days & covered_any:
            result[month] = NONE
        elif (any(len((days & active) - covered) >= MIN_UNCOVERED_DAYS
                  for active, covered in cards)
              or len((days & span) - covered_any) >= MIN_UNCOVERED_DAYS):
            result[month] = INCOMPLETE
        else:
            result[month] = COMPLETE
    return result


def coverage(db: Session, user_id: int) -> Dict[str, str]:
    """compute_coverage over the user's statements, first to last expense month."""
    first, last = (
        db.query(func.min(Expense.date), func.max(Expense.date))
        .filter(Expense.user_id == user_id)
        .one()
    )
    if first is None:
        return {}
    closings: Dict[str, List[date]] = defaultdict(list)
    for card_number, closing_date in (
        db.query(Statement.card_number, Statement.closing_date)
        .filter(Statement.user_id == user_id)
    ):
        closings[card_number].append(closing_date)
    return compute_coverage(closings, month_key(first), month_key(last))
