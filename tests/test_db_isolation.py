"""Guards the invariant that the suite never touches the real database.

tests/test_auth.py drives the Flask app, so it uses src.database.SessionLocal
rather than its own in-memory engine like the other test modules do. Its setUp
methods call db.query(User).delete(). Before this guard existed, that deleted
every user from the developer's real data/expenses.db and left fixtures behind
(regression introduced in d8e437c, Phase 2a).
"""

import os
import tempfile
import unittest

from src import config
from src.database import engine


def _production_db_path():
    """The default database path src.config would use with no override."""
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(repo_root, 'data', 'expenses.db')


class DatabaseIsolationTest(unittest.TestCase):
    def test_config_does_not_point_at_the_real_database(self):
        self.assertNotIn(
            _production_db_path(),
            config.DATABASE_URL,
            'the test suite is configured against the real database; '
            'tests/__init__.py must redirect DATABASE_URL',
        )

    def test_engine_does_not_point_at_the_real_database(self):
        self.assertNotIn(_production_db_path(), str(engine.url))

    def test_test_database_lives_outside_the_repository(self):
        """A throwaway path, so a stray write can never land in the working tree.

        Deliberately not asserting that the real database file is absent: on a
        developer machine it legitimately exists. The invariant is that the suite
        is never pointed at it, which the two tests above cover.
        """
        repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        db_path = str(engine.url).replace('sqlite:///', '')
        self.assertFalse(db_path.startswith(repo_root))
        self.assertTrue(db_path.startswith(tempfile.gettempdir()))
