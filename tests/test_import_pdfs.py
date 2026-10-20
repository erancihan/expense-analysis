"""Tests for the batch PDF importer (src/import_pdfs.py).

The headline case is the multi-card regression: two statement PDFs that share a
single Gmail message id (one email, two cards) must BOTH be imported. The old
dedup keyed the file-skip on the email id alone and silently dropped the second
card's transactions.
"""

import unittest
from datetime import date, datetime
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.models import Base, Expense, ProcessedEmail, Statement, User
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
        user = User(email='owner@example.com', password_hash='x', is_admin=True, is_active=True)
        self.db.add(user)
        self.db.commit()
        self.uid = user.id

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

        stats = import_pdfs(self.db, user_id=self.uid)

        self.assertEqual(stats['new_inserted'], 2, 'both card statements must import')
        self.assertEqual(stats['files_skipped'], 0)
        rows = self.db.query(Expense).all()
        self.assertEqual({e.card_number for e in rows}, {'1111', '2222'})
        self.assertTrue(all(e.user_id == self.uid for e in rows))

        # The email is recorded exactly once as SUCCESS — no duplicate-key error.
        pe = self.db.query(ProcessedEmail).filter_by(message_id='MSG1').all()
        self.assertEqual(len(pe), 1)
        self.assertEqual(pe[0].status, 'SUCCESS')
        self.assertEqual(pe[0].user_id, self.uid)

    @patch('src.import_pdfs.IsbankParser')
    @patch('src.import_pdfs.glob.glob')
    def test_clear_wipes_expenses_but_keeps_account_and_rules(self, mock_glob, MockParser):
        """`make refresh-db` relies on this instead of deleting the database file.

        The Makefile target used to `rm -f data/expenses.db`, which destroyed the
        user account — and it had no create-user step, so the owner was left
        locked out. --clear rebuilds expense data in place, so accounts, tags and
        rules survive.
        """
        from src.models import ExpenseTag, Tag, TagRule

        tag = Tag(user_id=self.uid, name='grocery', color='#22c55e', icon='🛒', is_default=True)
        self.db.add(tag)
        self.db.flush()
        self.db.add(TagRule(user_id=self.uid, tag_id=tag.id, pattern='MIGROS',
                            match_type='contains', priority=10, is_default=True))
        old = Expense(user_id=self.uid, date=datetime(2020, 1, 1), description='STALE',
                      amount=5.0, currency='TRY', card_number='9999',
                      statement_period='2020-01', bank_source='isbank')
        self.db.add(old)
        self.db.flush()
        self.db.add(ExpenseTag(expense_id=old.id, tag_id=tag.id, source='auto'))
        self.db.add(ProcessedEmail(message_id='OLDMSG', status='SUCCESS', user_id=self.uid))
        self.db.commit()

        f1 = '/d/bank(isbank),date(2026-04-07),emailid(MSG1),card(1111).pdf'
        mock_glob.side_effect = lambda pat: [f1] if 'bank(' in pat else []
        MockParser.return_value.extract_transactions.return_value = [
            _tx('MIGROS MARKET', 10.0, '1111')
        ]

        import_pdfs(self.db, user_id=self.uid, clear=True)

        # Expense data rebuilt from scratch.
        descs = {e.description for e in self.db.query(Expense).all()}
        self.assertEqual(descs, {'MIGROS MARKET'})
        self.assertEqual(
            {p.message_id for p in self.db.query(ProcessedEmail).all()}, {'MSG1'})

        # Every tag link points at a live expense — no orphans left behind by the
        # clear. Deliberately not comparing against the deleted row's id: SQLite
        # reuses rowids, so the new expense can be handed the same id.
        linked = {t.expense_id for t in self.db.query(ExpenseTag).all()}
        live = {e.id: e.description for e in self.db.query(Expense).all()}
        self.assertTrue(set(linked) <= set(live), 'no tag link may dangle')
        self.assertEqual({live[i] for i in linked} or {'MIGROS MARKET'}, {'MIGROS MARKET'})

        # The things refresh-db used to destroy.
        self.assertEqual(self.db.query(User).count(), 1)
        self.assertEqual(self.db.query(User).one().email, 'owner@example.com')
        self.assertEqual(self.db.query(Tag).count(), 1)
        self.assertEqual(self.db.query(TagRule).count(), 1)

    @patch('src.import_pdfs.IsbankParser')
    @patch('src.import_pdfs.glob.glob')
    def test_email_from_prior_run_is_skipped(self, mock_glob, MockParser):
        self.db.add(ProcessedEmail(message_id='MSG1', status='SUCCESS', user_id=self.uid))
        self.db.commit()

        f1 = '/d/bank(isbank),date(2026-04-07),emailid(MSG1),card(1111).pdf'
        mock_glob.side_effect = lambda pat: [f1] if 'bank(' in pat else []
        MockParser.return_value.extract_transactions.side_effect = \
            lambda path: [_tx('X', 5.0, '1111')]

        stats = import_pdfs(self.db, user_id=self.uid)

        self.assertEqual(stats['files_skipped'], 1)
        self.assertEqual(stats['new_inserted'], 0)

    @patch('src.import_pdfs.IsbankParser')
    @patch('src.import_pdfs.glob.glob')
    def test_failed_email_is_retried_and_upgraded(self, mock_glob, MockParser):
        self.db.add(ProcessedEmail(message_id='MSG1', status='FAILED', user_id=self.uid))
        self.db.commit()

        f1 = '/d/bank(isbank),date(2026-04-07),emailid(MSG1),card(1111).pdf'
        mock_glob.side_effect = lambda pat: [f1] if 'bank(' in pat else []
        MockParser.return_value.extract_transactions.side_effect = \
            lambda path: [_tx('Y', 7.0, '1111')]

        stats = import_pdfs(self.db, user_id=self.uid)

        self.assertEqual(stats['new_inserted'], 1)
        pe = self.db.query(ProcessedEmail).filter_by(message_id='MSG1').all()
        self.assertEqual(len(pe), 1)
        self.assertEqual(pe[0].status, 'SUCCESS')

    @patch('src.import_pdfs.IsbankParser')
    @patch('src.import_pdfs.glob.glob')
    def test_resolves_owner_when_user_id_omitted(self, mock_glob, MockParser):
        # With a single admin present, import_pdfs resolves the owner itself.
        f1 = '/d/bank(isbank),date(2026-04-07),emailid(MSG9),card(1111).pdf'
        mock_glob.side_effect = lambda pat: [f1] if 'bank(' in pat else []
        MockParser.return_value.extract_transactions.side_effect = \
            lambda path: [_tx('Z', 9.0, '1111')]

        stats = import_pdfs(self.db)

        self.assertEqual(stats['new_inserted'], 1)
        self.assertEqual(self.db.query(Expense).one().user_id, self.uid)

    @patch('src.import_pdfs.IsbankParser')
    @patch('src.import_pdfs.glob.glob')
    def test_records_statement_source_file_and_email(self, mock_glob, MockParser):
        f1 = '/d/bank(isbank),date(2026-04-08),emailid(MSG1),card(1111).pdf'
        mock_glob.side_effect = lambda pat: [f1] if 'bank(' in pat else []
        tx = _tx('ACME MARKET', 10.0, '1111')
        tx['closing_date'] = date(2026, 4, 7)
        MockParser.return_value.extract_transactions.return_value = [tx]

        import_pdfs(self.db, user_id=self.uid)

        statement = self.db.query(Statement).one()
        self.assertEqual(statement.source_file, 'bank(isbank),date(2026-04-08),emailid(MSG1),card(1111).pdf')
        self.assertEqual(statement.email_id, 'MSG1')
        self.assertEqual(self.db.query(Expense).one().statement_id, statement.id)

    @patch('src.import_pdfs.IsbankParser')
    @patch('src.import_pdfs.glob.glob')
    def test_clear_also_removes_statements(self, mock_glob, MockParser):
        self.db.add(Statement(user_id=self.uid, card_number='9999', closing_date=date(2020, 1, 5),
                              period='2020-01', source_file='old.pdf'))
        self.db.commit()
        # One PDF with no transactions: import_pdfs returns before the clear
        # block when it finds no PDFs at all (src/import_pdfs.py:110-112).
        f1 = '/d/bank(isbank),date(2026-04-08),emailid(MSG1),card(1111).pdf'
        mock_glob.side_effect = lambda pat: [f1] if 'bank(' in pat else []
        MockParser.return_value.extract_transactions.return_value = []

        import_pdfs(self.db, user_id=self.uid, clear=True)

        self.assertEqual(self.db.query(Statement).count(), 0)


if __name__ == '__main__':
    unittest.main()
