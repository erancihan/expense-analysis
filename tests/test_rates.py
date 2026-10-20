"""Monthly FX and CPI series (src/analytics/rates.py). No network: HTTP is faked."""

import io
import unittest
from datetime import date
from unittest.mock import MagicMock, patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.analytics.rates import (
    CPI_TR, USDTRY, USER_AGENT, RateTable, carry_forward, fetch_cpi, fetch_fx,
    get_json, monthly_average, store,
)
from src.models import Base, Rate


class FakeFetch:
    """Records calls; returns a canned payload."""

    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def __call__(self, url, headers=None):
        self.calls.append((url, headers))
        return self.payload


class GetJsonTestCase(unittest.TestCase):
    def test_sends_an_explicit_user_agent_and_extra_headers(self):
        # Frankfurter answers 403 to urllib's default "Python-urllib/x.y".
        response = MagicMock()
        response.__enter__.return_value = io.BytesIO(b'{"ok": 1}')
        with patch('src.analytics.rates.urllib.request.urlopen', return_value=response) as urlopen:
            self.assertEqual(get_json('https://example.test/x', headers={'key': 'K'}), {'ok': 1})
        request = urlopen.call_args[0][0]
        self.assertEqual(request.get_header('User-agent'), USER_AGENT)
        self.assertEqual(request.get_header('Key'), 'K')


class MonthlyAverageTestCase(unittest.TestCase):
    def test_averages_days_per_month(self):
        self.assertEqual(
            monthly_average({'2024-01-02': 30.0, '2024-01-03': 32.0, '2024-02-01': 33.0}),
            {'2024-01': 31.0, '2024-02': 33.0})


class FetchFxTestCase(unittest.TestCase):
    def test_requests_the_range_and_averages_it(self):
        fetch = FakeFetch({'rates': {
            '2024-01-02': {'TRY': 30.0}, '2024-01-03': {'TRY': 32.0}, '2024-02-01': {'TRY': 33.0},
        }})
        result = fetch_fx('USD', date(2024, 1, 1), date(2024, 2, 29), fetch=fetch)
        self.assertEqual(result, {'2024-01': 31.0, '2024-02': 33.0})
        self.assertEqual(fetch.calls[0][0],
                         'https://api.frankfurter.dev/v1/2024-01-01..2024-02-29?from=USD&to=TRY')


class FetchCpiTestCase(unittest.TestCase):
    def test_parses_items_and_skips_missing_values(self):
        fetch = FakeFetch({'items': [
            {'Tarih': '2024-1', 'TP_TUKFIY2025_GENEL': '60.5', 'UNIXTIME': {}},
            {'Tarih': '2024-2', 'TP_TUKFIY2025_GENEL': 63.0},
            {'Tarih': '2024-3', 'TP_TUKFIY2025_GENEL': None},
        ]})
        result = fetch_cpi('SECRET', date(2024, 1, 1), date(2024, 3, 31), fetch=fetch)
        self.assertEqual(result, {'2024-01': 60.5, '2024-02': 63.0})
        url, headers = fetch.calls[0]
        self.assertTrue(url.startswith('https://evds3.tcmb.gov.tr/igmevdsms-dis/series=TP.TUKFIY2025.GENEL'))
        self.assertIn('startDate=01-01-2024', url)
        self.assertIn('endDate=31-03-2024', url)
        self.assertIn('frequency=5', url)
        self.assertEqual(headers, {'key': 'SECRET'})

    def test_accepts_other_date_layouts(self):
        fetch = FakeFetch({'items': [
            {'Tarih': '01-2024', 'TP_TUKFIY2025_GENEL': '1'},
            {'Tarih': '15-02-2024', 'TP_TUKFIY2025_GENEL': '2'},
        ]})
        self.assertEqual(fetch_cpi('K', date(2024, 1, 1), date(2024, 2, 29), fetch=fetch),
                         {'2024-01': 1.0, '2024-02': 2.0})


class CarryForwardTestCase(unittest.TestCase):
    def test_fills_months_after_the_last_known_as_provisional(self):
        self.assertEqual(carry_forward({'2026-06': 10.0, '2026-07': 11.0}, '2026-09'), {
            '2026-06': (10.0, False), '2026-07': (11.0, False),
            '2026-08': (11.0, True), '2026-09': (11.0, True),
        })

    def test_empty_stays_empty(self):
        self.assertEqual(carry_forward({}, '2026-09'), {})


class StoreTestCase(unittest.TestCase):
    def test_upserts(self):
        engine = create_engine('sqlite:///:memory:')
        Base.metadata.create_all(engine)
        db = sessionmaker(bind=engine)()
        store(db, USDTRY, {'2026-08': (40.0, True)})
        db.commit()
        store(db, USDTRY, {'2026-08': (41.0, False), '2026-09': (42.0, True)})
        db.commit()
        rows = {(r.month, r.value, r.provisional) for r in db.query(Rate)}
        self.assertEqual(rows, {('2026-08', 41.0, False), ('2026-09', 42.0, True)})
        db.close()


class RateTableTestCase(unittest.TestCase):
    def setUp(self):
        self.rates = RateTable(
            {USDTRY: {'2024-01': 30.0, '2026-08': 48.0},
             CPI_TR: {'2024-01': 50.0, '2026-08': 100.0}},
            provisional={(CPI_TR, '2026-08')},
        )

    def test_to_try(self):
        self.assertEqual(self.rates.to_try(10.0, 'TRY', '2024-01'), 10.0)
        self.assertEqual(self.rates.to_try(10.0, None, '2024-01'), 10.0)
        self.assertEqual(self.rates.to_try(10.0, 'USD', '2024-01'), 300.0)
        self.assertIsNone(self.rates.to_try(10.0, 'USD', '2025-01'))   # no rate that month
        self.assertIsNone(self.rates.to_try(10.0, 'GBP', '2024-01'))   # no series

    def test_to_real_is_in_latest_months_lira(self):
        self.assertEqual(self.rates.to_real(100.0, '2024-01'), 200.0)
        self.assertEqual(self.rates.to_real(100.0, '2026-08'), 100.0)
        self.assertIsNone(self.rates.to_real(100.0, '2019-01'))

    def test_to_usd(self):
        self.assertEqual(self.rates.to_usd(300.0, '2024-01'), 10.0)
        self.assertIsNone(self.rates.to_usd(300.0, '2019-01'))

    def test_metadata(self):
        self.assertTrue(self.rates.has(CPI_TR))
        self.assertFalse(self.rates.has('EURTRY'))
        self.assertEqual(self.rates.latest(USDTRY), '2026-08')
        self.assertIsNone(self.rates.latest('EURTRY'))
        self.assertTrue(self.rates.is_provisional(CPI_TR, '2026-08'))
        self.assertFalse(self.rates.is_provisional(USDTRY, '2026-08'))

    def test_without_cpi_real_is_unavailable(self):
        self.assertIsNone(RateTable({}).to_real(100.0, '2024-01'))

    def test_load_from_database(self):
        engine = create_engine('sqlite:///:memory:')
        Base.metadata.create_all(engine)
        db = sessionmaker(bind=engine)()
        store(db, USDTRY, {'2024-01': (30.0, False), '2024-02': (31.0, True)})
        db.commit()
        rates = RateTable.load(db)
        self.assertEqual(rates.value(USDTRY, '2024-01'), 30.0)
        self.assertTrue(rates.is_provisional(USDTRY, '2024-02'))
        db.close()


if __name__ == '__main__':
    unittest.main()
