"""The /insights page shell and the shared nav partial."""

import unittest

from werkzeug.security import generate_password_hash

from src.database import SessionLocal, engine
from src.extensions import limiter
from src.models import Base, User
from src.web import app


class InsightsPageTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        Base.metadata.create_all(engine)

    def setUp(self):
        db = SessionLocal()
        try:
            db.query(User).delete()
            db.add(User(email='owner@example.com', password_hash=generate_password_hash('pw'), is_active=True))
            db.commit()
        finally:
            db.close()
        app.config['TESTING'] = True
        app.config['WTF_CSRF_ENABLED'] = False
        limiter.enabled = False
        self.client = app.test_client()
        self.client.post('/login', data={'email': 'owner@example.com', 'password': 'pw'})

    def test_anonymous_is_redirected_to_login(self):
        self.client.post('/logout')
        r = self.client.get('/insights')
        self.assertEqual(r.status_code, 302)
        self.assertIn('/login', r.headers['Location'])

    def test_page_renders_with_its_script_and_chart_slots(self):
        r = self.client.get('/insights')
        self.assertEqual(r.status_code, 200)
        html = r.data.decode()
        self.assertIn('/static/insights.js', html)
        self.assertIn('x-data="insights()"', html)
        for element_id in ('monthlyChart', 'annualChart', 'mixChart'):
            self.assertIn(f'id="{element_id}"', html)
        self.assertNotIn('settingsModalOpen = true', html)

    def test_both_pages_share_the_nav(self):
        insights = self.client.get('/insights').data.decode()
        dashboard = self.client.get('/').data.decode()
        self.assertIn('href="/"', insights)
        self.assertIn('href="/insights"', dashboard)
        self.assertIn('settingsModalOpen = true', dashboard)
        self.assertIn('/static/app.js', dashboard)

    def test_static_script_is_served(self):
        self.assertEqual(self.client.get('/static/insights.js').status_code, 200)

    def test_page_has_the_yoy_table_and_mix_drill_out(self):
        html = self.client.get('/insights').data.decode()
        self.assertIn('id="yoyTable"', html)
        self.assertIn('All categories', html)
