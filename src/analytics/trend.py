""""Am I spending more?" — the trend maths behind the first insights panel.

The panel works on one dataset (`load_spend`) and applies the lens afterwards,
so nominal lira, today's lira and USD are three views of the same rows rather
than three queries that could drift apart.

Months whose statements are missing are the trap here. A month with half its
statements looks like a frugal month, so:

  * a `none` month has no total at all (a gap, never a zero);
  * rolling averages only average `complete` months;
  * every year-over-year comparison pairs a month with the same month a year
    earlier and drops the pair when either side is not `complete`, so a hole
    lowers neither window.

Everything "current" (YTD, the last 12 months) keys off the last month asked
for, not the wall clock, which keeps these functions deterministic.
"""

import math
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from typing import Dict, Iterable, List, Optional, Tuple

from src.analytics.coverage import COMPLETE, NONE
from src.analytics.dataset import SpendRow
from src.analytics.months import add_months, month_key, month_range
from src.analytics.rates import CPI_TR, USDTRY, RateTable

LENSES = (REAL, NOMINAL, USD) = ('real', 'nominal', 'usd')
ROLLING_WINDOWS = (3, 12)
UNTAGGED = 'untagged'
OTHER = 'other'

CPI_HINT = ("The today's-lira lens needs CPI data: set evds_api_key in "
            "config.local.yaml and run make fetch-rates.")


@dataclass(frozen=True)
class LensRow:
    month: str
    parent: str
    child: Optional[str]
    amount: float


def lens_available(lens: str, rates: RateTable) -> bool:
    if lens == NOMINAL:
        return True
    if lens == REAL:
        return rates.has(CPI_TR)
    if lens == USD:
        return rates.has(USDTRY)
    return False


def to_lens(rows: Iterable[SpendRow], lens: str, rates: RateTable) -> Tuple[List[LensRow], int]:
    """Rows converted into `lens`, plus how many were dropped for lack of a rate."""
    if lens not in LENSES:
        raise ValueError(f'lens must be one of {LENSES}, got {lens!r}')
    result, dropped = [], 0
    for row in rows:
        if lens == NOMINAL:
            amount = row.amount_try
        elif lens == REAL:
            amount = rates.to_real(row.amount_try, row.month)
        else:
            amount = rates.to_usd(row.amount_try, row.month)
        if amount is None:
            dropped += 1
            continue
        result.append(LensRow(month=row.month, parent=row.parent_tag or UNTAGGED,
                              child=row.child_tag, amount=amount))
    return result, dropped


def _sum_by_month(rows: Iterable[LensRow]) -> Dict[str, float]:
    sums: Dict[str, float] = defaultdict(float)
    for row in rows:
        sums[row.month] += row.amount
    return sums


def monthly_totals(rows: List[LensRow], coverage: Dict[str, str]) -> List[dict]:
    """One {'month', 'total', 'coverage'} per month; `none` months total None."""
    months = set(coverage) | {row.month for row in rows}
    if not months:
        return []
    sums = _sum_by_month(rows)
    result = []
    for month in month_range(min(months), max(months)):
        state = coverage.get(month, NONE)
        total = None if state == NONE else sums.get(month, 0.0)
        result.append({'month': month, 'total': total, 'coverage': state})
    return result


def rolling_average(monthly: List[dict], window: int) -> List[Optional[float]]:
    """Mean of the complete months in the `window` ending at each index.

    None until the window fits, and when fewer than half its months are
    complete (2 of 3, 6 of 12).
    """
    needed = math.ceil(window / 2)
    result: List[Optional[float]] = []
    for i in range(len(monthly)):
        if i < window - 1:
            result.append(None)
            continue
        totals = [m['total'] for m in monthly[i - window + 1:i + 1] if m['coverage'] == COMPLETE]
        result.append(sum(totals) / len(totals) if len(totals) >= needed else None)
    return result


def annual_totals(monthly: List[dict], last_month: str) -> List[dict]:
    years: Dict[int, dict] = {}
    for m in monthly:
        year = int(m['month'][:4])
        entry = years.setdefault(year, {'year': year, 'total': 0.0, 'months_covered': 0,
                                        'is_ytd': year == int(last_month[:4])})
        if m['total'] is not None:
            entry['total'] += m['total']
        if m['coverage'] == COMPLETE:
            entry['months_covered'] += 1
    return [years[year] for year in sorted(years)]


def quarter_key(month: str) -> str:
    return f'{month[:4]}-Q{(int(month[5:]) - 1) // 3 + 1}'


def _shares(sums: Dict[str, float]) -> Dict[str, float]:
    total = sum(sums.values())
    if total <= 0:
        return {}
    return {name: value / total for name, value in sums.items()}


def quarterly_mix(rows: List[LensRow], first_month: str, last_month: str,
                  parent: Optional[str] = None) -> List[dict]:
    """Per quarter, each parent's share of the net total (and, for `parent`,
    each child's share of that parent). A quarter netting ≤ 0 has no shares."""
    quarters: Dict[str, Tuple[dict, dict]] = {}
    for month in month_range(first_month, last_month):
        quarters.setdefault(quarter_key(month), (defaultdict(float), defaultdict(float)))
    for row in rows:
        if not first_month <= row.month <= last_month:
            continue
        by_parent, by_child = quarters[quarter_key(row.month)]
        name = row.parent or UNTAGGED
        by_parent[name] += row.amount
        if parent is not None and name == parent:
            by_child[row.child or OTHER] += row.amount
    return [{'quarter': quarter,
             'by_parent': _shares(by_parent),
             'by_child': _shares(by_child) if parent is not None else {}}
            for quarter, (by_parent, by_child) in quarters.items()]


def _shift(month: str, offset: int) -> str:
    return month_key(add_months(date(int(month[:4]), int(month[5:]), 1), offset))


def paired_months(months: List[str], coverage: Dict[str, str],
                  offset: int = 12) -> List[Tuple[str, str]]:
    """(m, m − offset) for each m, kept only when both months are complete."""
    pairs = []
    for month in months:
        prior = _shift(month, -offset)
        if coverage.get(month) == COMPLETE and coverage.get(prior) == COMPLETE:
            pairs.append((month, prior))
    return pairs


def pct_change(current: float, prior: float) -> Optional[float]:
    if prior <= 0:
        return None
    return (current - prior) / prior * 100


def _last_12(last_month: str) -> List[str]:
    return month_range(_shift(last_month, -11), last_month)


def _compare(rows: List[LensRow], pairs: List[Tuple[str, str]]) -> Optional[float]:
    if not pairs:
        return None
    sums = _sum_by_month(rows)
    current = sum(sums.get(m, 0.0) for m, _ in pairs)
    prior = sum(sums.get(p, 0.0) for _, p in pairs)
    return pct_change(current, prior)


def yoy_by_parent(rows: List[LensRow], coverage: Dict[str, str], last_month: str) -> List[dict]:
    pairs = paired_months(_last_12(last_month), coverage)
    recent = {m for m, _ in pairs}
    prior = {p for _, p in pairs}
    last_12: Dict[str, float] = defaultdict(float)
    prior_12: Dict[str, float] = defaultdict(float)
    seen = []
    for row in rows:
        name = row.parent or UNTAGGED
        if row.month in recent:
            last_12[name] += row.amount
        elif row.month in prior:
            prior_12[name] += row.amount
        else:
            continue
        if name not in seen:
            seen.append(name)
    result = [{'parent': name, 'last_12': last_12[name], 'prior_12': prior_12[name],
               'delta_pct': pct_change(last_12[name], prior_12[name])} for name in seen]
    result.sort(key=lambda r: (r['delta_pct'] is None, -abs(r['delta_pct'] or 0)))
    return result


def kpis(rows: List[LensRow], monthly: List[dict], coverage: Dict[str, str],
         last_month: str) -> dict:
    complete = [m['total'] for m in monthly if m['coverage'] == COMPLETE]
    last_pairs = paired_months(_last_12(last_month), coverage)
    ytd_pairs = paired_months(month_range(f'{last_month[:4]}-01', last_month), coverage)
    return {
        'last12_vs_prior12_pct': _compare(rows, last_pairs),
        'avg_monthly': sum(complete) / len(complete) if complete else None,
        'ytd_vs_prior_ytd_pct': _compare(rows, ytd_pairs),
        'months_compared': len(last_pairs),
    }


def build_trend(rows: List[SpendRow], coverage: Dict[str, str], lens: str, rates: RateTable,
                first_month: str, last_month: str, parent: Optional[str] = None) -> dict:
    """The /api/insights/trend payload, minus the echoed lens/basis/from/to.

    Monthly totals and rolling averages are computed over the whole history and
    then sliced, so a 12-month line still has values in a 12-month range.
    """
    lens_rows, dropped = to_lens(rows, lens, rates)
    if not first_month or not last_month:
        return {'monthly': [], 'rolling': {str(w): [] for w in ROLLING_WINDOWS},
                'annual': [], 'mix': [], 'yoy': [],
                'kpis': {'last12_vs_prior12_pct': None, 'avg_monthly': None,
                         'ytd_vs_prior_ytd_pct': None, 'months_compared': 0},
                'dropped_no_rate': dropped}

    full = monthly_totals(lens_rows, coverage)
    keep = [i for i, m in enumerate(full) if first_month <= m['month'] <= last_month]
    monthly = [full[i] for i in keep]
    rolling = {}
    for window in ROLLING_WINDOWS:
        values = rolling_average(full, window)
        rolling[str(window)] = [values[i] for i in keep]

    return {
        'monthly': monthly,
        'rolling': rolling,
        'annual': annual_totals(monthly, last_month),
        'mix': quarterly_mix(lens_rows, first_month, last_month, parent),
        'yoy': yoy_by_parent(lens_rows, coverage, last_month),
        'kpis': kpis(lens_rows, monthly, coverage, last_month),
        'dropped_no_rate': dropped,
    }
