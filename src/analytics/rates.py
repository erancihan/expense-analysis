"""Monthly FX and CPI series, for comparing spending across years.

Nominal lira mostly measures inflation over this data's span, so the insight
panels offer two lenses:

  real  — "today's lira": amount × CPI[latest] / CPI[month]
  usd   — amount / USDTRY[month]

Rows live in the `rates` table (src.models.Rate), filled by
scripts/fetch_rates.py:

  USDTRY, EURTRY  monthly average of ECB reference rates, from Frankfurter
                  (free, no key)
  CPI_TR          TÜİK CPI, 2025=100 — EVDS series TP.TUKFIY2025.GENEL (needs
                  a free EVDS key). TCMB moved EVDS to the evds3 host at the end
                  of 2025; the key goes in a `key` request header.
"""

import json
import urllib.request
from collections import defaultdict
from datetime import date
from typing import Callable, Dict, FrozenSet, Optional, Tuple

from sqlalchemy.orm import Session

from src.analytics.months import month_range
from src.models import Rate

USDTRY = 'USDTRY'
EURTRY = 'EURTRY'
CPI_TR = 'CPI_TR'
FX_SERIES = {'USD': USDTRY, 'EUR': EURTRY}

FRANKFURTER_URL = 'https://api.frankfurter.dev/v1/'
EVDS_URL = 'https://evds3.tcmb.gov.tr/igmevdsms-dis/'
EVDS_CPI_SERIES = 'TP.TUKFIY2025.GENEL'
# Frankfurter answers 403 to urllib's default "Python-urllib/x.y" agent.
USER_AGENT = 'expense-analysis/1.0'

GetJson = Callable[..., dict]


def get_json(url: str, headers: Optional[Dict[str, str]] = None) -> dict:
    """GET a JSON document with an explicit User-Agent."""
    request = urllib.request.Request(
        url, headers={'User-Agent': USER_AGENT, 'Accept': 'application/json', **(headers or {})})
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


def monthly_average(daily: Dict[str, float]) -> Dict[str, float]:
    """{'YYYY-MM-DD': value} → {'YYYY-MM': mean of that month's values}."""
    buckets = defaultdict(list)
    for day, value in daily.items():
        buckets[day[:7]].append(value)
    return {month: sum(values) / len(values) for month, values in sorted(buckets.items())}


def fetch_fx(currency: str, start: date, end: date, fetch: GetJson = get_json) -> Dict[str, float]:
    """Monthly average `currency`→TRY rate between `start` and `end`."""
    data = fetch(f'{FRANKFURTER_URL}{start.isoformat()}..{end.isoformat()}'
                 f'?from={currency}&to=TRY')
    return monthly_average(
        {day: rates['TRY'] for day, rates in data.get('rates', {}).items() if 'TRY' in rates})


def _evds_month(tarih: str) -> str:
    """EVDS 'Tarih' → 'YYYY-MM'. Monthly series use 'YYYY-M'; accept others too."""
    parts = [int(part) for part in tarih.split('-')]
    if len(parts) == 2:
        first, second = parts
        year, month = (first, second) if first > 12 else (second, first)
    elif len(parts) == 3:        # DD-MM-YYYY
        _, month, year = parts
    else:
        raise ValueError(f'Unrecognised EVDS date: {tarih!r}')
    return f'{year:04d}-{month:02d}'


def fetch_cpi(api_key: str, start: date, end: date, fetch: GetJson = get_json) -> Dict[str, float]:
    """Monthly CPI index between `start` and `end`. Months without a value are left out."""
    # EVDS takes its parameters in the path, with no '?'.
    url = (f'{EVDS_URL}series={EVDS_CPI_SERIES}'
           f'&startDate={start:%d-%m-%Y}&endDate={end:%d-%m-%Y}&type=json&frequency=5')
    data = fetch(url, headers={'key': api_key})
    column = EVDS_CPI_SERIES.replace('.', '_')
    values = {}
    for item in data.get('items', []):
        value = item.get(column)
        if value in (None, ''):
            continue
        values[_evds_month(item['Tarih'])] = float(value)
    return values


def carry_forward(values: Dict[str, float], through: str) -> Dict[str, Tuple[float, bool]]:
    """Known months as (value, False); months after the last one up to
    `through` repeat its value as (value, True) — provisional until published."""
    if not values:
        return {}
    result = {month: (value, False) for month, value in values.items()}
    last = max(values)
    for month in month_range(last, through)[1:]:
        result[month] = (values[last], True)
    return result


def store(db: Session, series: str, values: Dict[str, Tuple[float, bool]]) -> int:
    """Upsert (value, provisional) per month. The caller commits."""
    for month, (value, provisional) in values.items():
        db.merge(Rate(series=series, month=month, value=value, provisional=provisional))
    return len(values)


class RateTable:
    """In-memory view of the rates table, with the lens conversions."""

    def __init__(self, series: Dict[str, Dict[str, float]],
                 provisional: FrozenSet[Tuple[str, str]] = frozenset()):
        self.series = series
        self.provisional = set(provisional)

    @classmethod
    def load(cls, db: Session) -> 'RateTable':
        series: Dict[str, Dict[str, float]] = defaultdict(dict)
        provisional = set()
        for rate in db.query(Rate):
            series[rate.series][rate.month] = rate.value
            if rate.provisional:
                provisional.add((rate.series, rate.month))
        return cls(dict(series), frozenset(provisional))

    def has(self, series: str) -> bool:
        return bool(self.series.get(series))

    def latest(self, series: str) -> Optional[str]:
        months = self.series.get(series)
        return max(months) if months else None

    def value(self, series: str, month: str) -> Optional[float]:
        return self.series.get(series, {}).get(month)

    def is_provisional(self, series: str, month: str) -> bool:
        return (series, month) in self.provisional

    def to_try(self, amount: float, currency: Optional[str], month: str) -> Optional[float]:
        """`amount` in TRY, or None when there is no rate for that month."""
        if currency in (None, '', 'TRY'):
            return amount
        series = FX_SERIES.get(currency)
        rate = self.value(series, month) if series else None
        return amount * rate if rate else None

    def to_real(self, amount_try: float, month: str) -> Optional[float]:
        """TRY from `month` in the latest CPI month's lira."""
        latest = self.latest(CPI_TR)
        base = self.value(CPI_TR, month)
        if latest is None or not base:
            return None
        return amount_try * self.value(CPI_TR, latest) / base

    def to_usd(self, amount_try: float, month: str) -> Optional[float]:
        rate = self.value(USDTRY, month)
        return amount_try / rate if rate else None
