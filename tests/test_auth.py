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

from werkzeug.security import generate_password_hash

from src.models import Base, User
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


if __name__ == '__main__':
    unittest.main()
