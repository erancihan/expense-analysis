import unittest
from unittest.mock import patch

from cryptography.fernet import Fernet
from sqlalchemy import create_engine, text
from sqlalchemy.exc import StatementError
from sqlalchemy.orm import sessionmaker

from src.models import Base, User
from src.token_crypto import TokenEncryptionError

KEY_A = Fernet.generate_key().decode()
KEY_B = Fernet.generate_key().decode()
TOKEN = '{"refresh_token": "r"}'


class EncryptedTokenColumnTest(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine('sqlite:///:memory:')
        Base.metadata.create_all(self.engine)
        self.db = sessionmaker(bind=self.engine)()

    def tearDown(self):
        self.db.close()

    def _add_user(self, token):
        self.db.add(User(email='a@x', password_hash='x', is_active=True,
                         gmail_token=token))
        self.db.commit()
        self.db.expire_all()

    def _raw_token(self):
        return self.db.execute(
            text("select gmail_token from users where email='a@x'")
        ).scalar()

    @patch('src.config.TOKEN_ENCRYPTION_KEY', KEY_A)
    def test_stored_value_is_ciphertext(self):
        self._add_user(TOKEN)
        raw = self._raw_token()
        self.assertNotEqual(raw, TOKEN)
        self.assertTrue(raw.startswith('gAAAAA'))
        self.assertNotIn('refresh_token', raw)

    @patch('src.config.TOKEN_ENCRYPTION_KEY', KEY_A)
    def test_orm_read_returns_plaintext(self):
        self._add_user(TOKEN)
        user = self.db.query(User).filter_by(email='a@x').first()
        self.assertEqual(user.gmail_token, TOKEN)

    @patch('src.config.TOKEN_ENCRYPTION_KEY', KEY_A)
    def test_null_token_stays_null(self):
        self._add_user(None)
        self.assertIsNone(self._raw_token())
        user = self.db.query(User).filter_by(email='a@x').first()
        self.assertIsNone(user.gmail_token)
        self.assertFalse(user.gmail_connected)

    @patch('src.config.TOKEN_ENCRYPTION_KEY', KEY_A)
    def test_isnot_none_filter_still_matches(self):
        self._add_user(TOKEN)
        self.assertEqual(
            self.db.query(User).filter(User.gmail_token.isnot(None)).count(), 1
        )

    def test_unreadable_token_reads_as_none_without_raising(self):
        with patch('src.config.TOKEN_ENCRYPTION_KEY', KEY_A):
            self._add_user(TOKEN)
        with patch('src.config.TOKEN_ENCRYPTION_KEY', KEY_B):
            user = self.db.query(User).filter_by(email='a@x').first()
            self.assertIsNone(user.gmail_token)

    @patch('src.config.TOKEN_ENCRYPTION_KEY', '')
    def test_write_without_a_key_raises(self):
        with self.assertRaises(StatementError) as ctx:
            self._add_user(TOKEN)
        self.assertIsInstance(ctx.exception.orig, TokenEncryptionError)
        self.db.rollback()
