"""Direct per-expense tag assignment, and the untagged bucket in the summary.

Both exist because the tag hierarchy changed what the numbers mean:

- Assigning a tag by hand has to write the parent too, or the expense goes
  missing from a chart that stacks parent-level entries.
- "untagged" can no longer be derived on the client by subtracting by_tag from
  the period total, because by_tag holds parents *and* children.
"""

import unittest
from datetime import datetime

from werkzeug.security import generate_password_hash

from src.database import SessionLocal, engine
from src.extensions import limiter
from src.models import Base, Expense, ExpenseTag, Tag, TagRule, User
from src.tag_engine import TagEngine
from src.web import app


class ManualTaggingTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        Base.metadata.create_all(engine)

    def setUp(self):
        db = SessionLocal()
        try:
            db.query(ExpenseTag).delete()
            db.query(TagRule).delete()
            db.query(Expense).delete()
            db.query(Tag).delete()
            db.query(User).delete()
            db.commit()

            db.add(User(email='owner@example.com',
                        password_hash=generate_password_hash('pw'), is_active=True))
            db.add(User(email='other@example.com',
                        password_hash=generate_password_hash('pw'), is_active=True))
            db.commit()
            self.uid = db.query(User).filter_by(email='owner@example.com').one().id
            self.other_uid = db.query(User).filter_by(email='other@example.com').one().id

            food = Tag(user_id=self.uid, name='food', color='#f00', icon='🍽️')
            db.add(food)
            db.flush()
            self.food_id = food.id
            cafe = Tag(user_id=self.uid, name='cafe', color='#f80', icon='☕',
                       parent_id=food.id)
            flat = Tag(user_id=self.uid, name='misc', color='#888', icon='📦')
            db.add_all([cafe, flat])
            db.flush()
            self.cafe_id, self.flat_id = cafe.id, flat.id

            other_tag = Tag(user_id=self.other_uid, name='theirs', color='#0f0', icon='🌱')
            db.add(other_tag)
            db.flush()
            self.other_tag_id = other_tag.id

            exp = Expense(user_id=self.uid, date=datetime(2026, 1, 10),
                          description='MURAT HATUN', amount=100.0, currency='TRY',
                          bank_source='isbank', card_number='1111',
                          statement_period='2026-01', category='Uncategorized')
            db.add(exp)
            db.flush()
            self.exp_id = exp.id
            db.commit()
        finally:
            db.close()

        app.config['TESTING'] = True
        # Per-test, not module scope: tests/test_auth.py re-enables CSRF and the
        # limiter inside its own cases, and `app` is shared across files. Every
        # case here logs in, which trips the login rate limit on its own.
        app.config['WTF_CSRF_ENABLED'] = False
        limiter.enabled = False
        self.client = app.test_client()

    def _login(self, email='owner@example.com'):
        r = self.client.post('/login', data={'email': email, 'password': 'pw'})
        self.assertEqual(r.status_code, 302, 'login must succeed or these tests mean nothing')

    def _links(self, expense_id=None):
        """{tag_id: source} for an expense."""
        db = SessionLocal()
        try:
            return {et.tag_id: et.source for et in db.query(ExpenseTag)
                    .filter_by(expense_id=expense_id or self.exp_id).all()}
        finally:
            db.close()

    def _add(self, tag_id, expense_id=None):
        return self.client.post(
            f'/api/expenses/{expense_id or self.exp_id}/tags/{tag_id}')

    # ── assignment ───────────────────────────────────────────────────────────

    def test_assigning_a_child_also_records_its_parent(self):
        """The chart stacks parent-level entries; a child-only row is invisible."""
        self._login()
        self.assertEqual(self._add(self.cafe_id).status_code, 200)
        self.assertEqual(self._links(), {self.cafe_id: 'manual', self.food_id: 'manual'})

    def test_parent_row_is_manual_so_a_retag_cannot_drop_it(self):
        """retag_all() deletes every source='auto' row. An 'auto' parent written
        for a manual child would vanish and never come back — no rule matches
        it."""
        self._login()
        self._add(self.cafe_id)

        db = SessionLocal()
        try:
            TagEngine(db, self.uid).retag_all()
        finally:
            db.close()

        self.assertEqual(self._links(), {self.cafe_id: 'manual', self.food_id: 'manual'})

    def test_assigning_a_flat_tag_adds_one_row(self):
        self._login()
        self._add(self.flat_id)
        self.assertEqual(self._links(), {self.flat_id: 'manual'})

    def test_repeat_assignment_is_idempotent(self):
        self._login()
        self._add(self.cafe_id)
        self.assertEqual(self._add(self.cafe_id).status_code, 200)
        self.assertEqual(self._links(), {self.cafe_id: 'manual', self.food_id: 'manual'})

    def test_assigning_revives_a_suppressed_tag(self):
        self._login()
        self._add(self.cafe_id)
        self.client.delete(f'/api/expenses/{self.exp_id}/tags/{self.cafe_id}')
        self.assertEqual(self._links()[self.cafe_id], 'suppressed')

        self._add(self.cafe_id)
        self.assertEqual(self._links()[self.cafe_id], 'manual')

    def test_no_rule_is_created(self):
        """The whole point: tagging one expense must not affect any other."""
        self._login()
        self._add(self.cafe_id)
        self.assertEqual(self.client.get('/api/tag-rules').get_json(), [])

    def test_rejects_another_users_tag(self):
        self._login()
        self.assertEqual(self._add(self.other_tag_id).status_code, 404)
        self.assertEqual(self._links(), {})

    def test_rejects_another_users_expense(self):
        db = SessionLocal()
        try:
            theirs = Expense(user_id=self.other_uid, date=datetime(2026, 1, 11),
                             description='NOT MINE', amount=5.0, currency='TRY',
                             bank_source='isbank', card_number='2222',
                             statement_period='2026-01', category='Uncategorized')
            db.add(theirs)
            db.commit()
            theirs_id = theirs.id
        finally:
            db.close()

        self._login()
        self.assertEqual(self._add(self.cafe_id, expense_id=theirs_id).status_code, 404)
        self.assertEqual(self._links(theirs_id), {})

    def test_requires_authentication(self):
        self.assertEqual(self._add(self.cafe_id).status_code, 401)

    # ── untagged bucket ──────────────────────────────────────────────────────

    def _period(self):
        rows = self.client.get('/api/summary/monthly').get_json()
        return next(r for r in rows if r['label'] == '2026-01')

    def test_untagged_reports_the_expense(self):
        self._login()
        p = self._period()
        self.assertEqual(p['untagged'], 100.0)
        self.assertEqual(p['untagged_count'], 1)

    def test_untagged_drops_to_zero_once_tagged(self):
        self._login()
        self._add(self.cafe_id)
        p = self._period()
        self.assertEqual(p['untagged'], 0.0)
        self.assertEqual(p['untagged_count'], 0)

    def test_untagged_is_not_the_total_minus_by_tag(self):
        """The regression this replaces: by_tag holds cafe AND food for one
        expense, so total - sum(by_tag) goes negative and the old client-side
        subtraction clamped the untagged segment to zero."""
        db = SessionLocal()
        try:
            db.add(Expense(user_id=self.uid, date=datetime(2026, 1, 12),
                           description='NO TAG HERE', amount=40.0, currency='TRY',
                           bank_source='isbank', card_number='1111',
                           statement_period='2026-01', category='Uncategorized'))
            db.commit()
        finally:
            db.close()

        self._login()
        self._add(self.cafe_id)          # tags the 100.0 row at both levels
        p = self._period()

        self.assertEqual(p['total'], 140.0)
        self.assertEqual(p['untagged'], 40.0)
        by_tag_sum = sum(v['total'] for v in p['by_tag'].values())
        self.assertEqual(by_tag_sum, 200.0, 'parent + child are both present')
        self.assertLess(p['total'] - by_tag_sum, 0,
                        'the old subtraction would have produced a negative')

    def test_suppressed_tags_count_as_untagged(self):
        """Matches by_tag, which excludes suppressed rows — otherwise the
        expense would appear in no segment at all."""
        self._login()
        self._add(self.flat_id)
        self.client.delete(f'/api/expenses/{self.exp_id}/tags/{self.flat_id}')
        p = self._period()
        self.assertEqual(p['untagged'], 100.0)
        self.assertEqual(p['by_tag'], {})
