"""Server-side sorting for the expenses table.

Sorting must be server-side: /api/expenses is paginated, so a client-side sort
would reorder only the current page while looking correct.
"""

import unittest
from datetime import datetime

from werkzeug.security import generate_password_hash

from src.database import SessionLocal, engine
from src.models import Base, Expense, ExpenseTag, Tag, User
from src.web import app


class SortingTestCase(unittest.TestCase):
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

            user = User(email='sort@example.com',
                        password_hash=generate_password_hash('pw'), is_active=True)
            db.add(user)
            db.flush()
            self.uid = user.id

            food = Tag(user_id=self.uid, name='food', is_default=True)
            db.add(food)
            db.flush()
            cafe = Tag(user_id=self.uid, name='cafe', parent_id=food.id, is_default=True)
            db.add(cafe)
            db.flush()

            rows = [
                ('2026-01-03', 'ZEBRA SHOP', 30.0, '1111'),
                ('2026-01-01', 'ALPHA STORE', 20.0, '3333'),
                ('2026-01-02', 'MIDDLE MART', 10.0, '2222'),
            ]
            for d, desc, amt, card in rows:
                db.add(Expense(user_id=self.uid, date=datetime.strptime(d, '%Y-%m-%d'),
                               description=desc, amount=amt, currency='TRY',
                               bank_source='isbank', card_number=card,
                               statement_period='2026-01'))
            db.flush()
            tagged = db.query(Expense).filter_by(description='MIDDLE MART').one()
            db.add(ExpenseTag(expense_id=tagged.id, tag_id=cafe.id, source='auto'))
            db.commit()
        finally:
            db.close()
        app.config['TESTING'] = True
        # Set per-test rather than at module scope: `app` is shared across test
        # files and tests/test_auth.py re-enables CSRF inside one of its cases,
        # so a module-level assignment here would depend on test ordering.
        app.config['WTF_CSRF_ENABLED'] = False
        self.client = app.test_client()
        r = self.client.post('/login', data={'email': 'sort@example.com', 'password': 'pw'})
        self.assertEqual(r.status_code, 302, 'login must succeed for these tests to mean anything')

    def _descs(self, query=''):
        r = self.client.get('/api/expenses' + query)
        return [i['description'] for i in r.get_json()['items']]

    def test_default_is_newest_first(self):
        self.assertEqual(self._descs(), ['ZEBRA SHOP', 'MIDDLE MART', 'ALPHA STORE'])

    def test_sort_by_date_ascending(self):
        self.assertEqual(self._descs('?sort=date&dir=asc'),
                         ['ALPHA STORE', 'MIDDLE MART', 'ZEBRA SHOP'])

    def test_sort_by_amount(self):
        self.assertEqual(self._descs('?sort=amount&dir=asc'),
                         ['MIDDLE MART', 'ALPHA STORE', 'ZEBRA SHOP'])

    def test_sort_by_description(self):
        self.assertEqual(self._descs('?sort=description&dir=asc'),
                         ['ALPHA STORE', 'MIDDLE MART', 'ZEBRA SHOP'])

    def test_sort_by_card(self):
        self.assertEqual(self._descs('?sort=card&dir=asc'),
                         ['ZEBRA SHOP', 'MIDDLE MART', 'ALPHA STORE'])

    def test_untagged_expenses_sort_last_by_tag(self):
        """Only MIDDLE MART is tagged, so it leads in both directions."""
        self.assertEqual(self._descs('?sort=tag&dir=asc')[0], 'MIDDLE MART')
        self.assertEqual(self._descs('?sort=tag&dir=desc')[0], 'MIDDLE MART')

    def test_unknown_sort_falls_back_instead_of_erroring(self):
        r = self.client.get('/api/expenses?sort=nonsense&dir=sideways')
        self.assertEqual(r.status_code, 200)
        self.assertEqual([i['description'] for i in r.get_json()['items']],
                         ['ZEBRA SHOP', 'MIDDLE MART', 'ALPHA STORE'])

    def test_csv_export_honours_sort(self):
        r = self.client.get('/api/expenses/export.csv?sort=amount&dir=asc')
        self.assertEqual(r.status_code, 200)
        body = r.get_data(as_text=True)
        self.assertLess(body.index('MIDDLE MART'), body.index('ZEBRA SHOP'))
