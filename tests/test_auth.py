"""Tests for the authentication gate (src/auth.py + src/web.py wiring).

The app binds its engine from src.config at import time, so we point it at an
isolated temp DB *before* importing any src.* module.
"""

import os
import tempfile
import unittest

# Must run before importing src.* so src.config picks these up.
_TMPDIR = tempfile.mkdtemp()
os.environ['DATABASE_URL'] = f"sqlite:///{os.path.join(_TMPDIR, 'auth_test.db')}"
os.environ['SECRET_KEY'] = 'test-secret'
os.environ.pop('SESSION_COOKIE_SECURE', None)  # keep cookies usable over http in tests

from datetime import datetime

from werkzeug.security import generate_password_hash

from src.models import Base, User, Expense, Tag
from src.database import engine, SessionLocal
from src.web import app


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


if __name__ == '__main__':
    unittest.main()
