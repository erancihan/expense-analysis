"""Tests for the shared transaction ingestion (src/ingest.py).

This is the single code path used by BOTH the batch importer and the Gmail
processor, so covering it here exercises the core of both ingestion flows:
dedup, non-monetary-line skipping, and statement_period stamping.
"""

import unittest
from datetime import datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.models import Base, Expense
from src.ingest import ingest_transactions


def _tx(date='2026-04-07', description='MIGROS MARKET', amount=100.0,
        card_number='1111', currency='TRY', statement_period='2026-04',
        category='Uncategorized'):
    return {
        'date': datetime.strptime(date, '%Y-%m-%d'),
        'description': description,
        'amount': amount,
        'currency': currency,
        'category': category,
        'card_number': card_number,
        'statement_period': statement_period,
        'raw_text': f'{date} {description} {amount}',
    }


class IngestTestCase(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine('sqlite:///:memory:')
        Base.metadata.create_all(self.engine)
        self.db = sessionmaker(bind=self.engine)()

    def tearDown(self):
        self.db.close()

    def test_inserts_and_stamps_period(self):
        _, stats = ingest_transactions(self.db, [_tx()], bank_source='isbank')
        self.db.commit()
        self.assertEqual(stats['inserted'], 1)
        exp = self.db.query(Expense).one()
        self.assertEqual(exp.statement_period, '2026-04')
        self.assertEqual(exp.bank_source, 'isbank')

    def test_fallback_period_when_tx_has_none(self):
        # Parser couldn't find Hesap Kesim Tarihi → empty period; caller supplies
        # a fallback derived from the filename date.
        ingest_transactions(
            self.db, [_tx(statement_period='')], bank_source='isbank',
            fallback_period='2026-04')
        self.db.commit()
        self.assertEqual(self.db.query(Expense).one().statement_period, '2026-04')

    def test_skips_non_monetary_line(self):
        _, stats = ingest_transactions(
            self.db, [_tx(amount=None)], bank_source='isbank')
        self.assertEqual(stats['skipped_no_amount'], 1)
        self.assertEqual(stats['inserted'], 0)

    def test_dedup_within_batch(self):
        fps = set()
        ingest_transactions(self.db, [_tx()], bank_source='isbank', fingerprints=fps)
        _, stats = ingest_transactions(self.db, [_tx()], bank_source='isbank', fingerprints=fps)
        self.assertEqual(stats['duplicates'], 1)
        self.assertEqual(stats['inserted'], 0)

    def test_dedup_against_existing_rows(self):
        ingest_transactions(self.db, [_tx()], bank_source='isbank')
        self.db.commit()
        # No shared set passed → fingerprints are loaded from the DB.
        _, stats = ingest_transactions(self.db, [_tx()], bank_source='isbank')
        self.assertEqual(stats['duplicates'], 1)

    def test_same_amount_different_card_is_not_duplicate(self):
        fps = set()
        ingest_transactions(self.db, [_tx(card_number='1111')], bank_source='isbank', fingerprints=fps)
        _, stats = ingest_transactions(self.db, [_tx(card_number='2222')], bank_source='isbank', fingerprints=fps)
        self.assertEqual(stats['inserted'], 1)

    def test_dry_run_writes_nothing(self):
        _, stats = ingest_transactions(self.db, [_tx()], bank_source='isbank', dry_run=True)
        self.db.commit()
        self.assertEqual(stats['inserted'], 1)
        self.assertEqual(self.db.query(Expense).count(), 0)


if __name__ == '__main__':
    unittest.main()
