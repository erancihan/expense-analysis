"""Tests package.

Imported before any test module, so this is where suite-wide setup belongs
(`unittest` has no conftest.py).

Two things are set up here, and the order matters.

1. DATABASE_URL is redirected to a throwaway file. tests/test_auth.py drives the
   Flask app, so unlike the other test modules it uses src.database.SessionLocal
   instead of its own in-memory engine — and its setUp methods call
   db.query(User).delete(). Without this redirect, running the suite deletes
   every user from the real data/expenses.db and leaves fixtures behind.

   src/config.py reads os.getenv('DATABASE_URL') at import and src/database.py
   binds that value at its own import, so the environment variable must be set
   before either is imported. config.DATABASE_URL is then overwritten directly
   as well, because _load_local_config() would otherwise let a developer's
   config.local.yaml point the suite back at a real database.

2. A fixed Fernet key, because User.gmail_token is an EncryptedText column and
   any test that writes a token needs a usable key; without one the write raises
   StatementError. Tests that care about key handling patch
   src.config.TOKEN_ENCRYPTION_KEY themselves — token_crypto reads it at call
   time, so patching works.
"""

import atexit
import os
import shutil
import tempfile

_TEST_DB_DIR = tempfile.mkdtemp(prefix='expense-analysis-tests-')
atexit.register(shutil.rmtree, _TEST_DB_DIR, ignore_errors=True)
_TEST_DATABASE_URL = f"sqlite:///{os.path.join(_TEST_DB_DIR, 'test.db')}"

os.environ['DATABASE_URL'] = _TEST_DATABASE_URL

from cryptography.fernet import Fernet  # noqa: E402

from src import config  # noqa: E402

# Beats a local config.local.yaml that sets database_url.
config.DATABASE_URL = _TEST_DATABASE_URL

config.TOKEN_ENCRYPTION_KEY = Fernet.generate_key().decode()
