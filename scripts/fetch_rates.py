#!/usr/bin/env python3
"""
Fetch monthly FX (USDTRY, EURTRY) and CPI into the rates table.

FX comes from Frankfurter (ECB reference rates, no key). CPI comes from EVDS
(TCMB) and needs evds_api_key in config.local.yaml or EVDS_API_KEY; without it
only FX is fetched. Months after the last published value repeat it, marked
provisional, until a later run replaces them. Safe to re-run: rows are upserted.

Usage:
    ./scripts/fetch_rates.py
"""

import os
import sys
import urllib.error
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import func

from src import config
from src.analytics.months import month_key
from src.analytics.rates import CPI_TR, FX_SERIES, carry_forward, fetch_cpi, fetch_fx, store
from src.database import SessionLocal
from src.models import Expense


def _summary(series, values):
    if not values:
        return f"{series}: no data returned"
    return f"{series}: {len(values)} months, {min(values)} … {max(values)}"


def main():
    db = SessionLocal()
    try:
        first = db.query(func.min(Expense.date)).scalar()
        start = date(first.year, first.month, 1) if first else date(2019, 1, 1)
        end = date.today()
        through = month_key(end)
        try:
            for currency, series in FX_SERIES.items():
                monthly = fetch_fx(currency, start, end)
                store(db, series, carry_forward(monthly, through))
                print(_summary(series, monthly))

            if config.EVDS_API_KEY:
                cpi = fetch_cpi(config.EVDS_API_KEY, start, end)
                store(db, CPI_TR, carry_forward(cpi, through))
                print(_summary(CPI_TR, cpi))
                if cpi and min(cpi) > month_key(start):
                    print(f"  note: CPI starts at {min(cpi)}, after your first month "
                          f"{month_key(start)}; earlier months have no real-terms value.")
            else:
                print("CPI skipped: set evds_api_key in config.local.yaml (or EVDS_API_KEY) "
                      "to enable the \"today's lira\" lens.")
        except (urllib.error.URLError, ValueError, KeyError) as e:
            db.rollback()
            print(f"Error fetching rates: {e}", file=sys.stderr)
            sys.exit(1)
        db.commit()
    finally:
        db.close()


if __name__ == '__main__':
    main()
