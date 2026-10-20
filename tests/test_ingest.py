"""Tests for the shared transaction ingestion (src/ingest.py).

This is the single code path used by BOTH the batch importer and the Gmail
processor, so covering it here exercises the core of both ingestion flows:
dedup (scoped per user), non-monetary-line skipping, and statement_period
stamping.
"""

import unittest
from datetime import date, datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.models import Base, Expense, Statement
from src.ingest import ingest_transactions

USER_ID = 1
OTHER_USER_ID = 2


def _tx(date='2026-04-07', description='MIGROS MARKET', amount=100.0,
        card_number='1111', currency='TRY', statement_period='2026-04',
        category='Uncategorized', closing_date=None, installment=None):
    tx = {
        'date': datetime.strptime(date, '%Y-%m-%d'),
        'description': description,
        'amount': amount,
        'currency': currency,
        'category': category,
        'card_number': card_number,
        'statement_period': statement_period,
        'raw_text': f'{date} {description} {amount}',
        'closing_date': closing_date,
        'installment_no': None,
        'installment_count': None,
        'installment_total': None,
    }
    if installment:
        tx['installment_no'], tx['installment_count'], tx['installment_total'] = installment
    return tx


class IngestTestCase(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine('sqlite:///:memory:')
        Base.metadata.create_all(self.engine)
        self.db = sessionmaker(bind=self.engine)()

    def tearDown(self):
        self.db.close()

    def test_inserts_and_stamps_period(self):
        _, stats = ingest_transactions(self.db, [_tx()], user_id=USER_ID, bank_source='isbank')
        self.db.commit()
        self.assertEqual(stats['inserted'], 1)
        exp = self.db.query(Expense).one()
        self.assertEqual(exp.user_id, USER_ID)
        self.assertEqual(exp.statement_period, '2026-04')
        self.assertEqual(exp.bank_source, 'isbank')

    def test_fallback_period_when_tx_has_none(self):
        ingest_transactions(
            self.db, [_tx(statement_period='')], user_id=USER_ID, bank_source='isbank',
            fallback_period='2026-04')
        self.db.commit()
        self.assertEqual(self.db.query(Expense).one().statement_period, '2026-04')

    def test_skips_non_monetary_line(self):
        _, stats = ingest_transactions(
            self.db, [_tx(amount=None)], user_id=USER_ID, bank_source='isbank')
        self.assertEqual(stats['skipped_no_amount'], 1)
        self.assertEqual(stats['inserted'], 0)

    def test_dedup_across_batches(self):
        """Two separate calls = the same statement arriving twice → dedup."""
        fps = set()
        ingest_transactions(self.db, [_tx()], user_id=USER_ID, bank_source='isbank', fingerprints=fps)
        _, stats = ingest_transactions(self.db, [_tx()], user_id=USER_ID, bank_source='isbank', fingerprints=fps)
        self.assertEqual(stats['duplicates'], 1)
        self.assertEqual(stats['inserted'], 0)

    def test_repeated_identical_purchase_in_one_statement_is_kept(self):
        """Two identical rows printed on ONE statement are two real purchases.

        Found by a live run over 133 real İşbank statements: 52 transactions were
        being silently dropped this way — e.g. the same 6.89 transit fare twice in
        one day, which the statement prints as two rows. Deduping on
        (date|description|amount|card) alone cannot tell them apart, so the key
        also carries an occurrence index scoped to the statement.
        """
        _, stats = ingest_transactions(
            self.db, [_tx(), _tx()], user_id=USER_ID, bank_source='isbank')
        self.assertEqual(stats['inserted'], 2)
        self.assertEqual(stats['duplicates'], 0)
        self.db.commit()
        self.assertEqual(self.db.query(Expense).count(), 2)

    def test_re_ingesting_a_statement_with_repeats_still_dedups(self):
        """The repeat-preserving key must not defeat re-send dedup."""
        fps = set()
        ingest_transactions(self.db, [_tx(), _tx()], user_id=USER_ID,
                            bank_source='isbank', fingerprints=fps)
        _, stats = ingest_transactions(self.db, [_tx(), _tx()], user_id=USER_ID,
                                       bank_source='isbank', fingerprints=fps)
        self.assertEqual(stats['inserted'], 0)
        self.assertEqual(stats['duplicates'], 2)

    def test_repeats_dedup_against_rows_already_in_the_database(self):
        """Occurrence indices must be reconstructable from stored rows."""
        ingest_transactions(self.db, [_tx(), _tx()], user_id=USER_ID, bank_source='isbank')
        self.db.commit()
        # No shared set → fingerprints are rebuilt from the DB, which must
        # yield occurrence 1 AND 2 so a re-import of the same statement dedups.
        _, stats = ingest_transactions(self.db, [_tx(), _tx()], user_id=USER_ID,
                                       bank_source='isbank')
        self.assertEqual(stats['inserted'], 0)
        self.assertEqual(stats['duplicates'], 2)

    def test_dedup_against_existing_rows(self):
        ingest_transactions(self.db, [_tx()], user_id=USER_ID, bank_source='isbank')
        self.db.commit()
        # No shared set passed → fingerprints are loaded from the DB (this user).
        _, stats = ingest_transactions(self.db, [_tx()], user_id=USER_ID, bank_source='isbank')
        self.assertEqual(stats['duplicates'], 1)

    def test_identical_tx_for_different_users_not_deduped(self):
        # The same transaction belonging to two users must NOT collide.
        ingest_transactions(self.db, [_tx()], user_id=USER_ID, bank_source='isbank')
        self.db.commit()
        _, stats = ingest_transactions(self.db, [_tx()], user_id=OTHER_USER_ID, bank_source='isbank')
        self.assertEqual(stats['inserted'], 1)
        self.assertEqual(stats['duplicates'], 0)

    def test_same_amount_different_card_is_not_duplicate(self):
        fps = set()
        ingest_transactions(self.db, [_tx(card_number='1111')], user_id=USER_ID, bank_source='isbank', fingerprints=fps)
        _, stats = ingest_transactions(self.db, [_tx(card_number='2222')], user_id=USER_ID, bank_source='isbank', fingerprints=fps)
        self.assertEqual(stats['inserted'], 1)

    def test_dry_run_writes_nothing(self):
        _, stats = ingest_transactions(self.db, [_tx()], user_id=USER_ID, bank_source='isbank', dry_run=True)
        self.db.commit()
        self.assertEqual(stats['inserted'], 1)
        self.assertEqual(self.db.query(Expense).count(), 0)

    def test_links_expense_to_statement_and_copies_installments(self):
        ingest_transactions(
            self.db,
            [_tx(closing_date=date(2026, 4, 7), installment=(2, 3, 300.0))],
            user_id=USER_ID, bank_source='isbank',
            source_file='bank(isbank),date(2026-04-08),emailid(MSG1),card(1111).pdf',
            email_id='MSG1',
        )
        self.db.commit()

        statement = self.db.query(Statement).one()
        self.assertEqual(
            (statement.user_id, statement.card_number, statement.closing_date, statement.period),
            (USER_ID, '1111', date(2026, 4, 7), '2026-04'),
        )
        self.assertEqual(statement.email_id, 'MSG1')
        expense = self.db.query(Expense).one()
        self.assertEqual(expense.statement_id, statement.id)
        self.assertEqual(
            (expense.installment_no, expense.installment_count, expense.installment_total),
            (2, 3, 300.0),
        )

    def test_statement_is_reused_across_calls(self):
        for description in ('ACME ONE', 'ACME TWO'):
            ingest_transactions(
                self.db, [_tx(description=description, closing_date=date(2026, 4, 7))],
                user_id=USER_ID, bank_source='isbank')
        self.db.commit()
        self.assertEqual(self.db.query(Statement).count(), 1)
        self.assertEqual(len({e.statement_id for e in self.db.query(Expense)}), 1)

    def test_each_currency_gets_its_own_statement(self):
        ingest_transactions(
            self.db,
            [_tx(description='ACME TL', closing_date=date(2026, 4, 7)),
             _tx(description='ACME ONLINE', currency='USD', closing_date=date(2026, 4, 7))],
            user_id=USER_ID, bank_source='isbank')
        self.db.commit()
        self.assertEqual(
            sorted(s.currency for s in self.db.query(Statement)), ['TRY', 'USD'])
        by_desc = {e.description: e.statement.currency for e in self.db.query(Expense)}
        self.assertEqual(by_desc, {'ACME TL': 'TRY', 'ACME ONLINE': 'USD'})

    def test_no_closing_date_means_no_statement(self):
        ingest_transactions(self.db, [_tx()], user_id=USER_ID, bank_source='isbank')
        self.db.commit()
        self.assertEqual(self.db.query(Statement).count(), 0)
        self.assertIsNone(self.db.query(Expense).one().statement_id)

    def test_dry_run_creates_no_statement(self):
        ingest_transactions(
            self.db, [_tx(closing_date=date(2026, 4, 7))],
            user_id=USER_ID, bank_source='isbank', dry_run=True)
        self.db.commit()
        self.assertEqual(self.db.query(Statement).count(), 0)


if __name__ == '__main__':
    unittest.main()
