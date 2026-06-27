"""Tests for the rule-matching engine (src/tag_engine.py), including the
per-user scoping that keeps one user's rules from tagging another's expenses.
"""

import unittest
from datetime import datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.models import Base, User, Expense, Tag, TagRule, ExpenseTag
from src.tag_engine import TagEngine


class TagEngineTestCase(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine('sqlite:///:memory:')
        Base.metadata.create_all(self.engine)
        self.db = sessionmaker(bind=self.engine)()
        self.u1 = self._user('u1@x')
        self.u2 = self._user('u2@x')

    def tearDown(self):
        self.db.close()

    # ── helpers ───────────────────────────────────────────────────
    def _user(self, email):
        u = User(email=email, password_hash='x', is_active=True)
        self.db.add(u); self.db.commit()
        return u.id

    def _tag(self, user_id, name):
        t = Tag(user_id=user_id, name=name, color='#fff', icon='🏷️')
        self.db.add(t); self.db.commit()
        return t

    def _rule(self, user_id, tag, pattern, match_type='contains', priority=5, is_default=False):
        r = TagRule(user_id=user_id, tag_id=tag.id, pattern=pattern,
                    match_type=match_type, priority=priority, is_default=is_default)
        self.db.add(r); self.db.commit()
        return r

    def _expense(self, user_id, description):
        e = Expense(user_id=user_id, date=datetime(2026, 4, 7), description=description,
                    amount=10.0, currency='TRY', bank_source='isbank')
        self.db.add(e); self.db.commit()
        return e

    # ── match types ───────────────────────────────────────────────
    def test_contains_match(self):
        tag = self._tag(self.u1, 'grocery')
        self._rule(self.u1, tag, 'MIGROS', 'contains')
        exp = self._expense(self.u1, 'MIGROS MARKET ISTANBUL')
        matches = TagEngine(self.db, self.u1).match_expense(exp)
        self.assertEqual([m.tag_id for m in matches], [tag.id])

    def test_starts_with_match(self):
        tag = self._tag(self.u1, 'transport')
        self._rule(self.u1, tag, 'UBER', 'starts_with')
        self.assertTrue(TagEngine(self.db, self.u1).match_expense(self._expense(self.u1, 'UBER TRIP')))
        self.assertFalse(TagEngine(self.db, self.u1).match_expense(self._expense(self.u1, 'MY UBER')))

    def test_regex_match(self):
        tag = self._tag(self.u1, 'grocery')
        self._rule(self.u1, tag, r'\bA101\b', 'regex')
        self.assertTrue(TagEngine(self.db, self.u1).match_expense(self._expense(self.u1, 'A101 STORE')))
        self.assertFalse(TagEngine(self.db, self.u1).match_expense(self._expense(self.u1, 'PA101X')))

    # ── precedence ────────────────────────────────────────────────
    def test_highest_priority_rule_wins_per_tag(self):
        tag = self._tag(self.u1, 'grocery')
        self._rule(self.u1, tag, 'MIG', priority=1)
        hi = self._rule(self.u1, tag, 'MIGROS', priority=99)
        matches = TagEngine(self.db, self.u1).match_expense(self._expense(self.u1, 'MIGROS'))
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0].id, hi.id)

    def test_user_rule_overrides_default_rule(self):
        default_tag = self._tag(self.u1, 'shopping')
        user_tag = self._tag(self.u1, 'grocery')
        self._rule(self.u1, default_tag, 'MIGROS', is_default=True)
        self._rule(self.u1, user_tag, 'MIGROS', is_default=False)
        matches = TagEngine(self.db, self.u1).match_expense(self._expense(self.u1, 'MIGROS'))
        # User rule shadows the default → only the user's tag is applied.
        self.assertEqual([m.tag_id for m in matches], [user_tag.id])

    # ── apply / suppress ──────────────────────────────────────────
    def test_apply_rules_skips_suppressed(self):
        tag = self._tag(self.u1, 'grocery')
        self._rule(self.u1, tag, 'MIGROS')
        exp = self._expense(self.u1, 'MIGROS')
        self.db.add(ExpenseTag(expense_id=exp.id, tag_id=tag.id, source='suppressed'))
        self.db.commit()
        applied = TagEngine(self.db, self.u1).apply_rules([exp])
        self.assertEqual(applied, 0)  # not re-added over a suppression

    def test_retag_preserves_manual_tags(self):
        tag = self._tag(self.u1, 'grocery')
        manual = self._tag(self.u1, 'special')
        self._rule(self.u1, tag, 'MIGROS')
        exp = self._expense(self.u1, 'MIGROS')
        self.db.add(ExpenseTag(expense_id=exp.id, tag_id=manual.id, source='manual'))
        self.db.commit()
        TagEngine(self.db, self.u1).retag_all()
        sources = {et.tag_id: et.source for et in
                   self.db.query(ExpenseTag).filter_by(expense_id=exp.id).all()}
        self.assertEqual(sources.get(manual.id), 'manual')  # preserved
        self.assertEqual(sources.get(tag.id), 'auto')       # re-applied

    # ── isolation ─────────────────────────────────────────────────
    def test_rules_are_scoped_to_user(self):
        tag = self._tag(self.u1, 'grocery')
        self._rule(self.u1, tag, 'MIGROS')
        # u2 has the same merchant but no rules of their own.
        exp2 = self._expense(self.u2, 'MIGROS')
        self.assertEqual(TagEngine(self.db, self.u2).match_expense(exp2), [])

    def test_retag_only_touches_own_expenses(self):
        t1 = self._tag(self.u1, 'grocery'); self._rule(self.u1, t1, 'MIGROS')
        e1 = self._expense(self.u1, 'MIGROS')
        e2 = self._expense(self.u2, 'MIGROS')
        TagEngine(self.db, self.u1).retag_all()
        self.assertEqual(self.db.query(ExpenseTag).filter_by(expense_id=e1.id).count(), 1)
        self.assertEqual(self.db.query(ExpenseTag).filter_by(expense_id=e2.id).count(), 0)

    def test_stats_scoped_to_user(self):
        t1 = self._tag(self.u1, 'grocery'); self._rule(self.u1, t1, 'MIGROS')
        self._expense(self.u1, 'MIGROS')
        self._expense(self.u2, 'MIGROS')  # belongs to u2, must not count
        eng = TagEngine(self.db, self.u1); eng.retag_all()
        stats = eng.get_stats()
        self.assertEqual(stats['total_expenses'], 1)
        self.assertEqual(stats['tagged'], 1)


if __name__ == '__main__':
    unittest.main()
