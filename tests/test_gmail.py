"""Tests for per-user Gmail wiring (src/gmail_client.py + src/processor.py).

The live OAuth handshake needs Google, so the Google client libraries are mocked
here; these cover the wiring: building a client from a stored token, and the
processor iterating connected users (vs the legacy single-account fallback).
"""

import unittest
from unittest.mock import patch, MagicMock

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.models import Base, User


class GmailClientFromTokenTestCase(unittest.TestCase):
    @patch('src.gmail_client.build')
    @patch('src.gmail_client.Credentials')
    def test_from_token_json_builds_service(self, MockCredentials, mock_build):
        from src.gmail_client import GmailClient
        fake_creds = MagicMock(valid=True)
        fake_creds.to_json.return_value = '{"token":"abc"}'
        MockCredentials.from_authorized_user_info.return_value = fake_creds

        client = GmailClient.from_token_json('{"refresh_token":"r"}')

        MockCredentials.from_authorized_user_info.assert_called_once()
        mock_build.assert_called_once()
        self.assertEqual(client.token_json, '{"token":"abc"}')

    @patch('src.gmail_client.build')
    @patch('src.gmail_client.Credentials')
    def test_expired_token_is_refreshed(self, MockCredentials, mock_build):
        from src.gmail_client import GmailClient
        fake_creds = MagicMock(valid=False, expired=True, refresh_token='r')
        MockCredentials.from_authorized_user_info.return_value = fake_creds

        GmailClient.from_token_json('{"refresh_token":"r"}')

        fake_creds.refresh.assert_called_once()


class ProcessorMultiUserTestCase(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine('sqlite:///:memory:')
        Base.metadata.create_all(self.engine)
        self.db = sessionmaker(bind=self.engine)()

    def tearDown(self):
        self.db.close()

    def _fake_get_db(self):
        yield self.db

    @patch('src.processor.GmailClient')
    def test_iterates_each_connected_user(self, MockGmail):
        from src.processor import ExpenseProcessor
        self.db.add(User(email='a@x', password_hash='x', is_active=True, gmail_token='{"t":"a"}'))
        self.db.add(User(email='b@x', password_hash='x', is_active=True, gmail_token='{"t":"b"}'))
        self.db.add(User(email='c@x', password_hash='x', is_active=True))  # not connected
        self.db.commit()

        client = MockGmail.from_token_json.return_value
        client.fetch_emails.return_value = []
        client.token_json = None

        with patch('src.processor.get_db', self._fake_get_db):
            ExpenseProcessor().process()

        # Built a per-user client for the two connected users only.
        self.assertEqual(MockGmail.from_token_json.call_count, 2)
        MockGmail.assert_not_called()  # global file-based client not used

    @patch('src.processor.resolve_owner')
    @patch('src.processor.GmailClient')
    def test_falls_back_to_legacy_when_none_connected(self, MockGmail, mock_resolve):
        from src.processor import ExpenseProcessor
        self.db.add(User(email='admin@x', password_hash='x', is_active=True, is_admin=True))
        self.db.commit()
        mock_resolve.return_value = MagicMock(id=1, email='admin@x')
        MockGmail.return_value.fetch_emails.return_value = []

        with patch('src.processor.get_db', self._fake_get_db):
            ExpenseProcessor().process()

        MockGmail.assert_called_once()              # global client built
        MockGmail.from_token_json.assert_not_called()


if __name__ == '__main__':
    unittest.main()
