"""Tests for two-level tag hierarchy (schema, seeding, engine).

Both the child and its parent are stored on each expense. That makes
`?tag=<parent>` work with no query change, at the cost that summing across all
tag rows double-counts — so consumers must pick a single level.
"""

import os
import tempfile
import unittest
from datetime import datetime

import yaml
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.models import Base, Expense, ExpenseTag, Tag, TagRule
from src.seeding import seed_default_tags

USER_ID = 1


def _write(tmpdir, tags, rules=None):
    path = os.path.join(tmpdir, 'tags.yaml')
    with open(path, 'w', encoding='utf-8') as f:
        yaml.safe_dump({'tags': tags, 'rules': rules or []}, f, allow_unicode=True)
    return path


class TagHierarchyTest(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine('sqlite:///:memory:')
        Base.metadata.create_all(self.engine)
        self.db = sessionmaker(bind=self.engine)()
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmpdir = self._tmp.name

    def tearDown(self):
        self.db.close()

    def _tag(self, name):
        return self.db.query(Tag).filter_by(user_id=USER_ID, name=name).one()

    def test_child_is_linked_to_its_parent(self):
        path = _write(self.tmpdir, [
            {'name': 'food'},
            {'name': 'grocery', 'parent': 'food'},
        ])
        seed_default_tags(self.db, USER_ID, path)

        food, grocery = self._tag('food'), self._tag('grocery')
        self.assertIsNone(food.parent_id)
        self.assertEqual(grocery.parent_id, food.id)
        self.assertEqual(grocery.parent.name, 'food')
        self.assertEqual({c.name for c in food.children}, {'grocery'})

    def test_to_dict_exposes_the_parent(self):
        path = _write(self.tmpdir, [
            {'name': 'food'},
            {'name': 'grocery', 'parent': 'food'},
        ])
        seed_default_tags(self.db, USER_ID, path)
        self.assertEqual(self._tag('grocery').to_dict()['parent_name'], 'food')
        self.assertIsNone(self._tag('food').to_dict()['parent_name'])

    def test_parent_declared_after_child_still_links(self):
        """Order in the YAML must not matter — parents are resolved in a first pass."""
        path = _write(self.tmpdir, [
            {'name': 'grocery', 'parent': 'food'},
            {'name': 'food'},
        ])
        seed_default_tags(self.db, USER_ID, path)
        self.assertEqual(self._tag('grocery').parent.name, 'food')

    def test_depth_three_is_rejected(self):
        path = _write(self.tmpdir, [
            {'name': 'food'},
            {'name': 'grocery', 'parent': 'food'},
            {'name': 'organic', 'parent': 'grocery'},
        ])
        with self.assertRaises(ValueError) as ctx:
            seed_default_tags(self.db, USER_ID, path)
        self.assertIn('grocery', str(ctx.exception))

    def test_unknown_parent_is_rejected(self):
        path = _write(self.tmpdir, [{'name': 'grocery', 'parent': 'nope'}])
        with self.assertRaises(ValueError):
            seed_default_tags(self.db, USER_ID, path)


class ParentTaggingTest(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine('sqlite:///:memory:')
        Base.metadata.create_all(self.engine)
        self.db = sessionmaker(bind=self.engine)()
        food = Tag(user_id=USER_ID, name='food', is_default=True)
        self.db.add(food)
        self.db.flush()
        grocery = Tag(user_id=USER_ID, name='grocery', parent_id=food.id, is_default=True)
        self.db.add(grocery)
        self.db.flush()
        self.db.add(TagRule(user_id=USER_ID, tag_id=grocery.id, pattern='MIGROS',
                            match_type='contains', priority=10, is_default=True))
        self.food_id, self.grocery_id = food.id, grocery.id
        self.db.commit()

    def tearDown(self):
        self.db.close()

    def _expense(self, description='MIGROS MARKET'):
        e = Expense(user_id=USER_ID, date=datetime(2026, 4, 7), description=description,
                    amount=100.0, currency='TRY', bank_source='isbank',
                    card_number='1111', statement_period='2026-04')
        self.db.add(e)
        self.db.commit()
        return e

    def _tag_ids(self, expense_id):
        return {t.tag_id for t in self.db.query(ExpenseTag).filter_by(expense_id=expense_id).all()}

    def test_matching_a_child_also_applies_its_parent(self):
        from src.tag_engine import TagEngine
        e = self._expense()
        TagEngine(self.db, user_id=USER_ID).apply_rules([e])
        self.assertEqual(self._tag_ids(e.id), {self.grocery_id, self.food_id})

    def test_rerunning_adds_no_duplicate_parent(self):
        from src.tag_engine import TagEngine
        e = self._expense()
        engine = TagEngine(self.db, user_id=USER_ID)
        engine.apply_rules([e])
        engine.apply_rules([e])
        rows = self.db.query(ExpenseTag).filter_by(expense_id=e.id).all()
        self.assertEqual(len(rows), 2, 'child + parent exactly once')

    def test_expense_contributes_at_most_once_to_a_single_parent(self):
        """The double-count guard.

        Deliberately NOT asserting that parent totals equal total spend: an
        expense under two different parents counts in both, which is inherent to
        multi-category expenses.
        """
        from src.tag_engine import TagEngine
        cafe = Tag(user_id=USER_ID, name='cafe', parent_id=self.food_id, is_default=True)
        self.db.add(cafe)
        self.db.flush()
        self.db.add(TagRule(user_id=USER_ID, tag_id=cafe.id, pattern='MARKET',
                            match_type='contains', priority=5, is_default=True))
        self.db.commit()

        e = self._expense('MIGROS MARKET')   # matches both grocery and cafe
        TagEngine(self.db, user_id=USER_ID).apply_rules([e])

        parent_rows = [t for t in self.db.query(ExpenseTag).filter_by(expense_id=e.id).all()
                       if t.tag_id == self.food_id]
        self.assertEqual(len(parent_rows), 1,
                         'two children of one parent must still yield ONE parent row')

    def test_suppressing_a_child_leaves_the_parent(self):
        from src.tag_engine import TagEngine
        e = self._expense()
        TagEngine(self.db, user_id=USER_ID).apply_rules([e])
        child = self.db.query(ExpenseTag).filter_by(expense_id=e.id, tag_id=self.grocery_id).one()
        child.source = 'suppressed'
        self.db.commit()
        parent = self.db.query(ExpenseTag).filter_by(expense_id=e.id, tag_id=self.food_id).one()
        self.assertEqual(parent.source, 'auto')
