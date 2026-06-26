"""Tests for the batch PDF importer (src/import_pdfs.py).

The headline case is the multi-card regression: two statement PDFs that share a
single Gmail message id (one email, two cards) must BOTH be imported. The old
dedup keyed the file-skip on the email id alone and silently dropped the second
card's transactions.
"""

import unittest
from datetime import datetime
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.models import Base, Expense, ProcessedEmail
from src.import_pdfs import import_pdfs, parse_pdf_filename


def _tx(description, amount, card_number, date='2026-04-07'):
    return {
        'date': datetime.strptime(date, '%Y-%m-%d'),
        'description': description,
        'amount': amount,
        'currency': 'TRY',
        'category': 'Uncategorized',
        'card_number': card_number,
        'statement_period': '2026-04',
        'raw_text': '',
    }


class ParseFilenameTestCase(unittest.TestCase):
    def test_new_structured_format(self):
        f = parse_pdf_filename('bank(isbank),date(2026-04-07),emailid(MSG1),card(6152).pdf')
        self.assertEqual(f['bank'], 'isbank')
        self.assertEqual(f['date'], '2026-04-07')
        self.assertEqual(f['emailid'], 'MSG1')
        self.assertEqual(f['card'], '6152')

    def test_old_underscore_format_has_no_emailid(self):
        f = parse_pdf_filename('isbank_2019-06-07_124_original.pdf')
        self.assertEqual(f['bank'], 'isbank')
        self.assertEqual(f['date'], '2019-06-07')
        self.assertNotIn('emailid', f)


class ImportPdfsTestCase(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine('sqlite:///:memory:')
        Base.metadata.create_all(self.engine)
        self.db = sessionmaker(bind=self.engine)()

    def tearDown(self):
        self.db.close()

    @patch('src.import_pdfs.IsbankParser')
    @patch('src.import_pdfs.glob.glob')
    def test_two_cards_same_email_both_import(self, mock_glob, MockParser):
        f1 = '/d/bank(isbank),date(2026-04-07),emailid(MSG1),card(1111).pdf'
        f2 = '/d/bank(isbank),date(2026-04-07),emailid(MSG1),card(2222).pdf'
        mock_glob.side_effect = lambda pat: [f1, f2] if 'bank(' in pat else []

        def extract(path):
            if 'card(1111)' in path:
                return [_tx('CARD A PURCHASE', 10.0, '1111')]
            return [_tx('CARD B PURCHASE', 20.0, '2222')]
        MockParser.return_value.extract_transactions.side_effect = extract

        stats = import_pdfs(self.db)

        self.assertEqual(stats['new_inserted'], 2, 'both card statements must import')
        self.assertEqual(stats['files_skipped'], 0)
        cards = {e.card_number for e in self.db.query(Expense).all()}
        self.assertEqual(cards, {'1111', '2222'})

        # The email is recorded exactly once as SUCCESS — no duplicate-key error.
        rows = self.db.query(ProcessedEmail).filter_by(message_id='MSG1').all()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].status, 'SUCCESS')

    @patch('src.import_pdfs.IsbankParser')
    @patch('src.import_pdfs.glob.glob')
    def test_email_from_prior_run_is_skipped(self, mock_glob, MockParser):
        # Simulate a prior successful run.
        self.db.add(ProcessedEmail(message_id='MSG1', status='SUCCESS'))
        self.db.commit()

        f1 = '/d/bank(isbank),date(2026-04-07),emailid(MSG1),card(1111).pdf'
        mock_glob.side_effect = lambda pat: [f1] if 'bank(' in pat else []
        MockParser.return_value.extract_transactions.side_effect = \
            lambda path: [_tx('X', 5.0, '1111')]

        stats = import_pdfs(self.db)

        self.assertEqual(stats['files_skipped'], 1)
        self.assertEqual(stats['new_inserted'], 0)

    @patch('src.import_pdfs.IsbankParser')
    @patch('src.import_pdfs.glob.glob')
    def test_failed_email_is_retried_and_upgraded(self, mock_glob, MockParser):
        # A FAILED row from a prior run must not block a later SUCCESS, and the
        # upsert must not raise a duplicate-key error.
        self.db.add(ProcessedEmail(message_id='MSG1', status='FAILED'))
        self.db.commit()

        f1 = '/d/bank(isbank),date(2026-04-07),emailid(MSG1),card(1111).pdf'
        mock_glob.side_effect = lambda pat: [f1] if 'bank(' in pat else []
        MockParser.return_value.extract_transactions.side_effect = \
            lambda path: [_tx('Y', 7.0, '1111')]

        stats = import_pdfs(self.db)

        self.assertEqual(stats['new_inserted'], 1)
        rows = self.db.query(ProcessedEmail).filter_by(message_id='MSG1').all()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].status, 'SUCCESS')


if __name__ == '__main__':
    unittest.main()
