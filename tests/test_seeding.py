"""Tests for per-user seeding (src/seeding.py).

Seeding used to be insert-only, which made `data/seeds/tags.yaml` misleading:
editing a rule's priority or match_type, or deleting a rule outright, had no
effect on an already-seeded user. Both old and new versions stayed live and
fired together. Found while tuning rules against 133 real statements.

Reconciliation is deliberately limited to RULES. Stale default *tags* are left
alone: Tag.expense_tags cascades delete-orphan (src/models.py:115), so removing
a tag would silently destroy every assignment of it, including ones applied by
hand.
"""

import os
import tempfile
import unittest

import yaml
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.models import Base, Tag, TagRule
from src.seeding import seed_default_tags

USER_ID = 1
OTHER_USER_ID = 2


def _seed_file(tmpdir, rules, tags=None):
    tags = tags or [{'name': 'grocery', 'color': '#22c55e', 'icon': '🛒'},
                    {'name': 'cafe', 'color': '#78716c', 'icon': '☕'}]
    path = os.path.join(tmpdir, 'tags.yaml')
    with open(path, 'w', encoding='utf-8') as f:
        yaml.safe_dump({'tags': tags, 'rules': rules}, f, allow_unicode=True)
    return path


class SeedingReconcileTest(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine('sqlite:///:memory:')
        Base.metadata.create_all(self.engine)
        self.db = sessionmaker(bind=self.engine)()
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmpdir = self._tmp.name

    def tearDown(self):
        self.db.close()

    def _rules(self, user_id=USER_ID):
        return {
            (r.tag.name, r.pattern): (r.match_type, r.priority, r.is_default)
            for r in self.db.query(TagRule).filter_by(user_id=user_id).all()
        }

    def test_creates_tags_and_rules(self):
        path = _seed_file(self.tmpdir, [
            {'tag': 'grocery', 'pattern': 'MIGROS', 'match_type': 'contains', 'priority': 10},
        ])
        counts = seed_default_tags(self.db, USER_ID, path)
        self.assertEqual(counts['tags'], 2)
        self.assertEqual(counts['rules'], 1)
        self.assertIn(('grocery', 'MIGROS'), self._rules())

    def test_stale_default_rule_is_removed(self):
        first = _seed_file(self.tmpdir, [
            {'tag': 'grocery', 'pattern': 'MIGROS', 'match_type': 'contains', 'priority': 10},
            {'tag': 'grocery', 'pattern': 'GONE', 'match_type': 'contains', 'priority': 10},
        ])
        seed_default_tags(self.db, USER_ID, first)
        self.assertIn(('grocery', 'GONE'), self._rules())

        second = os.path.join(self.tmpdir, 'second.yaml')
        with open(second, 'w', encoding='utf-8') as f:
            yaml.safe_dump({
                'tags': [{'name': 'grocery'}, {'name': 'cafe'}],
                'rules': [{'tag': 'grocery', 'pattern': 'MIGROS',
                           'match_type': 'contains', 'priority': 10}],
            }, f, allow_unicode=True)
        counts = seed_default_tags(self.db, USER_ID, second)

        self.assertEqual(counts['removed'], 1)
        self.assertNotIn(('grocery', 'GONE'), self._rules())
        self.assertIn(('grocery', 'MIGROS'), self._rules())

    def test_user_created_rule_is_never_removed(self):
        path = _seed_file(self.tmpdir, [
            {'tag': 'grocery', 'pattern': 'MIGROS', 'match_type': 'contains', 'priority': 10},
        ])
        seed_default_tags(self.db, USER_ID, path)
        tag = self.db.query(Tag).filter_by(user_id=USER_ID, name='grocery').one()
        # A rule the user added through the UI: is_default=False (src/web.py:640).
        self.db.add(TagRule(user_id=USER_ID, tag_id=tag.id, pattern='MY OWN RULE',
                            match_type='contains', priority=100, is_default=False))
        self.db.commit()

        counts = seed_default_tags(self.db, USER_ID, path)

        self.assertEqual(counts['removed'], 0)
        self.assertIn(('grocery', 'MY OWN RULE'), self._rules())

    def test_changed_match_type_and_priority_are_updated(self):
        first = _seed_file(self.tmpdir, [
            {'tag': 'grocery', 'pattern': 'SPORTIF', 'match_type': 'contains', 'priority': 5},
        ])
        seed_default_tags(self.db, USER_ID, first)
        self.assertEqual(self._rules()[('grocery', 'SPORTIF')], ('contains', 5, True))

        second = _seed_file(self.tmpdir, [
            {'tag': 'grocery', 'pattern': 'SPORTIF', 'match_type': 'regex', 'priority': 15},
        ])
        counts = seed_default_tags(self.db, USER_ID, second)

        self.assertEqual(counts['updated'], 1)
        self.assertEqual(self._rules()[('grocery', 'SPORTIF')], ('regex', 15, True))

    def test_reseeding_unchanged_file_reports_no_work(self):
        path = _seed_file(self.tmpdir, [
            {'tag': 'grocery', 'pattern': 'MIGROS', 'match_type': 'contains', 'priority': 10},
        ])
        seed_default_tags(self.db, USER_ID, path)
        counts = seed_default_tags(self.db, USER_ID, path)
        self.assertEqual(
            (counts['tags'], counts['rules'], counts['updated'], counts['removed']),
            (0, 0, 0, 0),
        )

    def test_default_tag_absent_from_yaml_is_kept(self):
        """Removing a tag would cascade-delete its expense assignments."""
        first = _seed_file(self.tmpdir, [], tags=[{'name': 'grocery'}, {'name': 'legacy'}])
        seed_default_tags(self.db, USER_ID, first)
        second = _seed_file(self.tmpdir, [], tags=[{'name': 'grocery'}])
        seed_default_tags(self.db, USER_ID, second)
        names = {t.name for t in self.db.query(Tag).filter_by(user_id=USER_ID).all()}
        self.assertIn('legacy', names)

    def test_reconcile_is_scoped_to_one_user(self):
        path = _seed_file(self.tmpdir, [
            {'tag': 'grocery', 'pattern': 'MIGROS', 'match_type': 'contains', 'priority': 10},
            {'tag': 'grocery', 'pattern': 'GONE', 'match_type': 'contains', 'priority': 10},
        ])
        seed_default_tags(self.db, USER_ID, path)
        seed_default_tags(self.db, OTHER_USER_ID, path)

        trimmed = _seed_file(self.tmpdir, [
            {'tag': 'grocery', 'pattern': 'MIGROS', 'match_type': 'contains', 'priority': 10},
        ])
        seed_default_tags(self.db, USER_ID, trimmed)

        self.assertNotIn(('grocery', 'GONE'), self._rules(USER_ID))
        self.assertIn(('grocery', 'GONE'), self._rules(OTHER_USER_ID))
