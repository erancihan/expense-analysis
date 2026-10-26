"""/api/insights/meta and /trend (src/insights_api.py). Synthetic data only."""

import unittest
from datetime import date, datetime

from werkzeug.security import generate_password_hash

from src.analytics.rates import CPI_TR, USDTRY
from src.analytics.trend import CPI_HINT
from src.database import SessionLocal, engine
from src.extensions import limiter
from src.models import Base, Expense, ExpenseTag, Rate, Statement, Tag, User
from src.web import app


class InsightsApiTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        Base.metadata.create_all(engine)

    def setUp(self):
        db = SessionLocal()
        try:
            for model in (ExpenseTag, Expense, Statement, Rate, Tag, User):
                db.query(model).delete()
            for email in ('owner@example.com', 'other@example.com', 'empty@example.com'):
                db.add(User(email=email, password_hash=generate_password_hash('pw'), is_active=True))
            db.commit()
            self.uid = db.query(User).filter_by(email='owner@example.com').one().id
            self.other_uid = db.query(User).filter_by(email='other@example.com').one().id

            self.food = Tag(user_id=self.uid, name='food', color='#111111')
            db.add(self.food); db.flush()
            db.add(Tag(user_id=self.uid, name='grocery', parent_id=self.food.id))
            # Statements closing on the 15th from Jan 2025 to Mar 2026, March 2025 missing.
            for month in self._months('2025-01', '2026-03'):
                if month == '2025-03':
                    continue
                db.add(Statement(user_id=self.uid, card_number='1234', currency='TRY',
                                 closing_date=date(int(month[:4]), int(month[5:]), 15),
                                 period=month))
            self._expense(db, self.uid, '2025-01-10', 100.0, tag=self.food)
            self._expense(db, self.uid, '2026-01-10', 150.0, tag=self.food)
            self._expense(db, self.other_uid, '2026-01-10', 9999.0)
            # Without a statement every month of theirs is 'none' and totals null.
            db.add(Statement(user_id=self.other_uid, card_number='5678', currency='TRY',
                             closing_date=date(2026, 1, 31), period='2026-01'))
            for month in self._months('2025-01', '2026-03'):
                db.add(Rate(series=USDTRY, month=month, value=10.0))
            db.commit()
        finally:
            db.close()
        app.config['TESTING'] = True
        app.config['WTF_CSRF_ENABLED'] = False
        limiter.enabled = False
        self.client = app.test_client()

    @staticmethod
    def _months(first, last):
        from src.analytics.months import month_range
        return month_range(first, last)

    def _expense(self, db, user_id, day, amount, tag=None):
        e = Expense(user_id=user_id, date=datetime.fromisoformat(day), description='ACME MARKET',
                    amount=amount, currency='TRY', card_number='1234', category='Uncategorized',
                    bank_source='isbank', statement_period=day[:7])
        db.add(e); db.flush()
        if tag is not None:
            db.add(ExpenseTag(expense_id=e.id, tag_id=tag.id, source='auto'))
        return e

    def _login(self, email='owner@example.com'):
        return self.client.post('/login', data={'email': email, 'password': 'pw'})

    # ── Gate ──────────────────────────────────────────────────────
    def test_requires_login(self):
        self.assertEqual(self.client.get('/api/insights/meta').status_code, 401)
        self.assertEqual(self.client.get('/api/insights/trend').status_code, 401)

    # ── Meta ──────────────────────────────────────────────────────
    def test_meta_reports_lenses_coverage_and_colors(self):
        self._login()
        meta = self.client.get('/api/insights/meta').get_json()
        self.assertEqual([l['id'] for l in meta['lenses']], ['real', 'nominal', 'usd'])
        real = meta['lenses'][0]
        self.assertFalse(real['available'])
        self.assertEqual(real['reason'], CPI_HINT)
        self.assertTrue(meta['lenses'][2]['available'])
        self.assertEqual(meta['default_lens'], 'nominal')
        self.assertEqual(meta['bases'], ['billed', 'purchased'])
        self.assertEqual(meta['rates'], {'cpi_latest': None, 'cpi_provisional': False, 'fx_latest': '2026-03'})
        self.assertEqual(meta['first_month'], '2025-01')
        self.assertEqual(meta['last_month'], '2026-01')
        self.assertEqual(meta['coverage']['2025-02'], 'incomplete')
        self.assertEqual(meta['tag_colors']['food'], '#111111')

    def test_meta_for_a_user_without_data(self):
        self._login('empty@example.com')
        meta = self.client.get('/api/insights/meta').get_json()
        self.assertEqual(meta['coverage'], {})
        self.assertIsNone(meta['first_month'])
        self.assertIsNone(meta['last_month'])

    def test_meta_prefers_real_lens_when_cpi_exists(self):
        db = SessionLocal()
        try:
            db.add(Rate(series=CPI_TR, month='2026-01', value=100.0, provisional=True))
            db.commit()
        finally:
            db.close()
        self._login()
        meta = self.client.get('/api/insights/meta').get_json()
        self.assertEqual(meta['default_lens'], 'real')
        self.assertEqual(meta['rates']['cpi_latest'], '2026-01')
        self.assertTrue(meta['rates']['cpi_provisional'])

    # ── Trend ─────────────────────────────────────────────────────
    def test_trend_defaults_and_shape(self):
        self._login()
        r = self.client.get('/api/insights/trend')
        self.assertEqual(r.status_code, 200)
        body = r.get_json()
        self.assertEqual((body['lens'], body['basis'], body['from'], body['to']),
                         ('nominal', 'billed', '2025-01', '2026-01'))
        for key in ('monthly', 'rolling', 'annual', 'mix', 'yoy', 'kpis', 'dropped_no_rate'):
            self.assertIn(key, body)
        self.assertEqual(body['monthly'][-1], {'month': '2026-01', 'total': 150.0, 'coverage': 'complete'})

    def test_trend_is_user_scoped(self):
        self._login('other@example.com')
        body = self.client.get('/api/insights/trend').get_json()
        self.assertEqual(sum(m['total'] or 0 for m in body['monthly']), 9999.0)

    def test_trend_usd_lens_and_range(self):
        self._login()
        body = self.client.get('/api/insights/trend?lens=usd&from=2026-01&to=2026-01').get_json()
        self.assertEqual([m['month'] for m in body['monthly']], ['2026-01'])
        self.assertEqual(body['monthly'][0]['total'], 15.0)

    def test_trend_rejects_bad_params(self):
        self._login()
        for query in ('lens=gold', 'basis=cash', 'from=2026-13', 'to=jan', 'from=2026-02&to=2026-01'):
            with self.subTest(query=query):
                r = self.client.get(f'/api/insights/trend?{query}')
                self.assertEqual(r.status_code, 400)
                self.assertIn('error', r.get_json())

    def test_trend_real_lens_without_cpi_names_the_fix(self):
        self._login()
        r = self.client.get('/api/insights/trend?lens=real')
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.get_json()['error'], CPI_HINT)

    def test_trend_unknown_parent_and_early_from_are_harmless(self):
        self._login()
        r = self.client.get('/api/insights/trend?parent=nope&from=2020-01')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.get_json()['mix'][0]['by_child'], {})
        self.assertEqual(r.get_json()['monthly'][0]['month'], '2025-01')

    def test_trend_for_a_user_without_data(self):
        self._login('empty@example.com')
        r = self.client.get('/api/insights/trend')
        self.assertEqual(r.status_code, 200)
        body = r.get_json()
        self.assertEqual(body['monthly'], [])
        self.assertIsNone(body['from'])
        self.assertIsNone(body['kpis']['avg_monthly'])
