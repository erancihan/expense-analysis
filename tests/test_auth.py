"""Tests for the authentication gate (src/auth.py + src/web.py wiring).

The app binds its engine from src.config at import time, so we point it at an
isolated temp DB *before* importing any src.* module.
"""

import os
import tempfile
import unittest
from unittest.mock import patch, MagicMock

# Must run before importing src.* so src.config picks these up.
_TMPDIR = tempfile.mkdtemp()
os.environ['DATABASE_URL'] = f"sqlite:///{os.path.join(_TMPDIR, 'auth_test.db')}"
os.environ['SECRET_KEY'] = 'test-secret'
os.environ.pop('SESSION_COOKIE_SECURE', None)  # keep cookies usable over http in tests

from datetime import datetime

from werkzeug.security import generate_password_hash

from src.models import Base, User, Expense, Tag, Budget
from src.database import engine, SessionLocal
from src.extensions import limiter
from src.web import app

# CSRF and rate limiting are verified in SecurityHardeningTestCase; disable them
# for the functional auth/isolation tests so requests don't need tokens and the
# many logins don't trip the limiter. (limiter.enabled is the runtime switch —
# RATELIMIT_ENABLED is only read at init_app time.)
app.config['WTF_CSRF_ENABLED'] = False
limiter.enabled = False


class AuthTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        Base.metadata.create_all(engine)

    def setUp(self):
        db = SessionLocal()
        try:
            db.query(User).delete()
            db.add(User(
                email='owner@example.com',
                password_hash=generate_password_hash('correct horse'),
                is_admin=True,
                is_active=True,
            ))
            db.commit()
        finally:
            db.close()
        app.config['TESTING'] = True
        self.client = app.test_client()

    def _login(self, email='owner@example.com', password='correct horse'):
        return self.client.post('/login', data={'email': email, 'password': password})

    # ── Gate ──────────────────────────────────────────────────────
    def test_api_requires_auth(self):
        r = self.client.get('/api/expenses')
        self.assertEqual(r.status_code, 401)

    def test_index_redirects_to_login_when_anonymous(self):
        r = self.client.get('/')
        self.assertEqual(r.status_code, 302)
        self.assertIn('/login', r.headers['Location'])

    def test_login_page_is_public(self):
        r = self.client.get('/login')
        self.assertEqual(r.status_code, 200)
        self.assertIn(b'Sign in', r.data)

    # ── Login / logout ────────────────────────────────────────────
    def test_login_then_api_ok(self):
        self.assertEqual(self._login().status_code, 302)
        self.assertEqual(self.client.get('/api/expenses').status_code, 200)

    def test_bad_password_rejected(self):
        self.assertEqual(self._login(password='wrong').status_code, 401)
        self.assertEqual(self.client.get('/api/expenses').status_code, 401)

    def test_inactive_user_cannot_login(self):
        db = SessionLocal()
        try:
            db.query(User).filter_by(email='owner@example.com').first().is_active = False
            db.commit()
        finally:
            db.close()
        self.assertEqual(self._login().status_code, 401)

    def test_logout_clears_session(self):
        self._login()
        self.assertEqual(self.client.get('/api/expenses').status_code, 200)
        self.client.post('/logout')
        self.assertEqual(self.client.get('/api/expenses').status_code, 401)

    def test_open_redirect_blocked(self):
        # ?next= to an external host must not be honoured.
        r = self.client.post('/login?next=https://evil.example/x',
                             data={'email': 'owner@example.com', 'password': 'correct horse'})
        self.assertEqual(r.status_code, 302)
        self.assertNotIn('evil.example', r.headers['Location'])


class IsolationTestCase(unittest.TestCase):
    """Two users must never see or touch each other's data."""

    @classmethod
    def setUpClass(cls):
        Base.metadata.create_all(engine)

    def setUp(self):
        db = SessionLocal()
        try:
            for model in (Expense, Tag, User):
                db.query(model).delete()
            db.commit()

            self.a = self._make_user(db, 'a@example.com')
            self.b = self._make_user(db, 'b@example.com')

            # One expense + one tag per user.
            self.exp_a = self._make_expense(db, self.a, 'A COFFEE', 10)
            self.exp_b = self._make_expense(db, self.b, 'B GROCERIES', 20)
            self.tag_a = self._make_tag(db, self.a, 'a_only')
            self.tag_b = self._make_tag(db, self.b, 'b_only')
        finally:
            db.close()

        app.config['TESTING'] = True
        self.client = app.test_client()

    @staticmethod
    def _make_user(db, email):
        u = User(email=email, password_hash=generate_password_hash('pw'), is_active=True)
        db.add(u)
        db.commit()
        return u.id

    @staticmethod
    def _make_expense(db, user_id, desc, amount):
        e = Expense(user_id=user_id, date=datetime(2026, 4, 7), description=desc,
                    amount=amount, currency='TRY', bank_source='isbank',
                    statement_period='2026-04', category='Uncategorized')
        db.add(e)
        db.commit()
        return e.id

    @staticmethod
    def _make_tag(db, user_id, name):
        t = Tag(user_id=user_id, name=name, color='#fff', icon='🏷️')
        db.add(t)
        db.commit()
        return t.id

    def _login(self, email):
        return self.client.post('/login', data={'email': email, 'password': 'pw'})

    def test_expenses_are_scoped(self):
        self._login('a@example.com')
        data = self.client.get('/api/expenses').get_json()
        descs = [it['description'] for it in data['items']]
        self.assertIn('A COFFEE', descs)
        self.assertNotIn('B GROCERIES', descs)
        self.assertEqual(data['total'], 1)

    def test_tags_are_scoped(self):
        self._login('a@example.com')
        names = [t['name'] for t in self.client.get('/api/tags').get_json()]
        self.assertEqual(names, ['a_only'])

    def test_cannot_read_other_users_expense(self):
        self._login('a@example.com')
        r = self.client.get(f'/api/expenses/{self.exp_b}/details')
        self.assertEqual(r.status_code, 404)

    def test_cannot_tag_other_users_expense(self):
        self._login('a@example.com')
        r = self.client.post(f'/api/expenses/{self.exp_b}/tags',
                             json={'tag_ids': [self.tag_a]})
        self.assertEqual(r.status_code, 404)

    def test_cannot_delete_other_users_tag(self):
        self._login('a@example.com')
        r = self.client.delete(f'/api/tags/{self.tag_b}')
        self.assertEqual(r.status_code, 404)
        # Confirm B's tag still exists.
        db = SessionLocal()
        try:
            self.assertIsNotNone(db.get(Tag, self.tag_b))
        finally:
            db.close()

    def test_same_tag_name_allowed_for_different_users(self):
        # Per-user uniqueness: A creating 'b_only' must not clash with B's.
        self._login('a@example.com')
        r = self.client.post('/api/tags', json={'name': 'b_only'})
        self.assertIn(r.status_code, (200, 201))

    def test_csv_export_is_scoped(self):
        self._login('a@example.com')
        r = self.client.get('/api/expenses/export.csv')
        self.assertEqual(r.status_code, 200)
        self.assertIn('text/csv', r.headers['Content-Type'])
        self.assertIn('attachment', r.headers['Content-Disposition'])
        body = r.get_data(as_text=True)
        self.assertIn('A COFFEE', body)
        self.assertNotIn('B GROCERIES', body)  # B's data must not leak

    def test_csv_export_neutralises_formula_injection(self):
        db = SessionLocal()
        try:
            db.add(Expense(user_id=self.a, date=datetime(2026, 4, 8),
                           description='=SUM(A1:A9)', amount=5, currency='TRY',
                           bank_source='isbank', statement_period='2026-04'))
            db.commit()
        finally:
            db.close()
        self._login('a@example.com')
        body = self.client.get('/api/expenses/export.csv').get_data(as_text=True)
        self.assertIn("'=SUM(A1:A9)", body)   # leading quote disarms the formula
        self.assertNotIn(',=SUM(A1:A9)', body)


class SecurityHardeningTestCase(unittest.TestCase):
    """CSRF, rate limiting and security headers (Phase 2b)."""

    @classmethod
    def setUpClass(cls):
        Base.metadata.create_all(engine)

    def setUp(self):
        db = SessionLocal()
        try:
            db.query(User).delete()
            db.add(User(email='owner@example.com',
                        password_hash=generate_password_hash('pw'), is_active=True))
            db.commit()
        finally:
            db.close()
        self.client = app.test_client()

    def tearDown(self):
        # Restore the relaxed defaults the other test classes rely on.
        app.config['WTF_CSRF_ENABLED'] = False
        limiter.enabled = False

    def test_csrf_rejects_tokenless_post(self):
        app.config['WTF_CSRF_ENABLED'] = True
        r = self.client.post('/login', data={'email': 'owner@example.com', 'password': 'pw'})
        self.assertEqual(r.status_code, 400)

    def test_login_page_carries_csrf_field(self):
        html = self.client.get('/login').get_data(as_text=True)
        self.assertIn('name="csrf_token"', html)

    def test_login_is_rate_limited(self):
        limiter.enabled = True
        codes = [
            self.client.post('/login', data={'email': 'x@x', 'password': 'bad'}).status_code
            for _ in range(15)
        ]
        self.assertIn(429, codes, 'login should be rate limited after repeated attempts')

    def test_security_headers_present(self):
        r = self.client.get('/login')
        self.assertEqual(r.headers.get('X-Content-Type-Options'), 'nosniff')
        self.assertEqual(r.headers.get('X-Frame-Options'), 'DENY')
        self.assertIn('Content-Security-Policy', r.headers)


class BudgetTestCase(unittest.TestCase):
    """Budget CRUD, upsert, validation and per-user scoping."""

    @classmethod
    def setUpClass(cls):
        Base.metadata.create_all(engine)

    def setUp(self):
        db = SessionLocal()
        try:
            for m in (Budget, Tag, Expense, User):
                db.query(m).delete()
            db.commit()
            a = User(email='a@x', password_hash=generate_password_hash('pw'), is_active=True)
            b = User(email='b@x', password_hash=generate_password_hash('pw'), is_active=True)
            db.add_all([a, b]); db.commit()
            self.a_id, self.b_id = a.id, b.id
            ta = Tag(user_id=a.id, name='grocery', color='#fff', icon='🛒')
            tb = Tag(user_id=b.id, name='grocery', color='#fff', icon='🛒')
            db.add_all([ta, tb]); db.commit()
            self.tag_a_id, self.tag_b_id = ta.id, tb.id
        finally:
            db.close()
        self.client = app.test_client()
        self.client.post('/login', data={'email': 'a@x', 'password': 'pw'})

    def test_create_and_list_overall_first(self):
        self.client.post('/api/budgets', json={'tag_id': None, 'amount': 5000})
        self.client.post('/api/budgets', json={'tag_id': self.tag_a_id, 'amount': 1200})
        items = self.client.get('/api/budgets').get_json()
        self.assertEqual(len(items), 2)
        self.assertIsNone(items[0]['tag_id'])  # overall first

    def test_upsert_updates_not_duplicates(self):
        self.client.post('/api/budgets', json={'tag_id': self.tag_a_id, 'amount': 1000})
        self.client.post('/api/budgets', json={'tag_id': self.tag_a_id, 'amount': 1500})
        items = self.client.get('/api/budgets').get_json()
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]['amount'], 1500)

    def test_rejects_non_positive_amount(self):
        self.assertEqual(self.client.post('/api/budgets', json={'tag_id': None, 'amount': 0}).status_code, 400)
        self.assertEqual(self.client.post('/api/budgets', json={'tag_id': None, 'amount': 'x'}).status_code, 400)

    def test_cannot_budget_other_users_tag(self):
        r = self.client.post('/api/budgets', json={'tag_id': self.tag_b_id, 'amount': 100})
        self.assertEqual(r.status_code, 404)

    def test_budgets_are_scoped(self):
        db = SessionLocal()
        try:
            db.add(Budget(user_id=self.b_id, tag_id=self.tag_b_id, amount=999)); db.commit()
            bid = db.query(Budget).filter_by(user_id=self.b_id).first().id
        finally:
            db.close()
        self.assertEqual(self.client.get('/api/budgets').get_json(), [])
        self.assertEqual(self.client.delete(f'/api/budgets/{bid}').status_code, 404)


class GmailWebTestCase(unittest.TestCase):
    """Per-user Gmail connect/callback/disconnect/status routes."""

    @classmethod
    def setUpClass(cls):
        Base.metadata.create_all(engine)

    def setUp(self):
        db = SessionLocal()
        try:
            db.query(User).delete()
            db.add(User(email='owner@example.com',
                        password_hash=generate_password_hash('pw'), is_active=True))
            db.commit()
        finally:
            db.close()
        self.client = app.test_client()
        self.client.post('/login', data={'email': 'owner@example.com', 'password': 'pw'})

    def _set_token(self, tok):
        db = SessionLocal()
        try:
            db.query(User).filter_by(email='owner@example.com').first().gmail_token = tok
            db.commit()
        finally:
            db.close()

    def _token(self):
        db = SessionLocal()
        try:
            return db.query(User).filter_by(email='owner@example.com').first().gmail_token
        finally:
            db.close()

    def test_status_reflects_connection(self):
        self.assertFalse(self.client.get('/api/gmail/status').get_json()['connected'])
        self._set_token('{"t":"x"}')
        self.assertTrue(self.client.get('/api/gmail/status').get_json()['connected'])

    def test_disconnect_clears_token(self):
        self._set_token('{"t":"x"}')
        self.client.post('/gmail/disconnect')
        self.assertIsNone(self._token())

    @patch('src.web.gmail_oauth.credentials_available', return_value=False)
    def test_connect_without_credentials_redirects_home(self, _mock):
        r = self.client.get('/gmail/connect')
        self.assertEqual(r.status_code, 302)
        self.assertTrue(r.headers['Location'].endswith('/'))

    @patch('src.web.gmail_oauth.build_flow')
    def test_callback_stores_token(self, mock_build_flow):
        flow = MagicMock()
        flow.credentials.to_json.return_value = '{"t":"fresh"}'
        mock_build_flow.return_value = flow
        r = self.client.get('/gmail/callback?code=abc&state=xyz')
        self.assertEqual(r.status_code, 302)
        self.assertEqual(self._token(), '{"t":"fresh"}')


if __name__ == '__main__':
    unittest.main()
