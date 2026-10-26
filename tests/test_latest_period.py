"""/api/summary/latest-period — the month the dashboard opens on.

The frontend used to open on the current calendar month, which is always empty:
the statement covering it has not been issued yet. This endpoint reports the
newest statement period that actually holds data.
"""

import unittest
from datetime import datetime

from werkzeug.security import generate_password_hash

from src.database import SessionLocal, engine
from src.extensions import limiter
from src.models import Base, Expense, ExpenseTag, Tag, User
from src.web import app


class LatestPeriodTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        Base.metadata.create_all(engine)

    def setUp(self):
        db = SessionLocal()
        try:
            db.query(ExpenseTag).delete()
            db.query(Expense).delete()
            db.query(Tag).delete()
            db.query(User).delete()
            db.commit()

            for email in ('owner@example.com', 'other@example.com'):
                db.add(User(email=email,
                            password_hash=generate_password_hash('pw'), is_active=True))
            db.commit()
            self.uid = db.query(User).filter_by(email='owner@example.com').one().id
            self.other_uid = db.query(User).filter_by(email='other@example.com').one().id
        finally:
            db.close()

        app.config['TESTING'] = True
        # Per-test, not module scope: `app` is shared and tests/test_auth.py
        # re-enables CSRF and the limiter inside its own cases.
        app.config['WTF_CSRF_ENABLED'] = False
        limiter.enabled = False
        self.client = app.test_client()

    def _login(self, email='owner@example.com'):
        r = self.client.post('/login', data={'email': email, 'password': 'pw'})
        self.assertEqual(r.status_code, 302, 'login must succeed or these tests mean nothing')

    def _add(self, period, user_id=None, category='Uncategorized', day='2026-01-15'):
        db = SessionLocal()
        try:
            db.add(Expense(user_id=user_id or self.uid,
                           date=datetime.strptime(day, '%Y-%m-%d'),
                           description='SHOP', amount=10.0, currency='TRY',
                           bank_source='isbank', card_number='1111',
                           statement_period=period, category=category))
            db.commit()
        finally:
            db.close()

    def _period(self):
        r = self.client.get('/api/summary/latest-period')
        self.assertEqual(r.status_code, 200)
        return r.get_json()['period']

    def test_returns_newest_period(self):
        for p in ('2026-03', '2026-07', '2026-05'):
            self._add(p)
        self._login()
        self.assertEqual(self._period(), '2026-07')

    def test_null_when_no_expenses(self):
        """A fresh account gets null, which the frontend reads as 'use today'."""
        self._login()
        self.assertIsNone(self._period())

    def test_scoped_to_the_current_user(self):
        self._add('2026-02')
        self._add('2026-09', user_id=self.other_uid)
        self._login()
        self.assertEqual(self._period(), '2026-02')

    def test_payment_only_period_is_ignored(self):
        """Mirrors api_summary_monthly, which excludes payments — otherwise the
        dashboard would open on a month with no bar in the chart."""
        self._add('2026-04')
        self._add('2026-06', category='Payment')
        self._login()
        self.assertEqual(self._period(), '2026-04')

    def test_requires_authentication(self):
        self._add('2026-04')
        r = self.client.get('/api/summary/latest-period')
        self.assertEqual(r.status_code, 401)

    def test_agrees_with_the_last_bar_of_the_chart(self):
        """The whole point: the selected month must be the chart's last period."""
        for p in ('2026-05', '2026-06', '2026-07'):
            self._add(p)
        self._add('2026-08', category='Payment')
        self._login()
        chart = self.client.get('/api/summary/monthly').get_json()
        self.assertEqual(self._period(), chart[-1]['label'])
