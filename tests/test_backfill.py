"""Tests for the in-place statement backfill (src/backfill.py)."""

import unittest
from collections import Counter
from datetime import date, datetime
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import src.backfill
from src.backfill import TagSetChanged, backfill_statements
from src.models import Base, Expense, ExpenseTag, Statement, Tag, User

FIRST = '/d/bank(isbank),date(2026-05-06),emailid(MSG1),card(1111).pdf'
RESENT = '/d/bank(isbank),date(2026-05-07),emailid(MSG2),card(1111).pdf'
USD_FILE = '/d/bank(isbank),date(2026-05-06),emailid(MSG3),card(1111).pdf'


def _tx(day, description, amount, card='1111', closing=date(2026, 5, 5), installment=None,
        currency='TRY'):
    tx = {
        'date': datetime.strptime(day, '%Y-%m-%d'),
        'description': description,
        'amount': amount,
        'currency': currency,
        'card_number': card,
        'closing_date': closing,
        'statement_period': closing.strftime('%Y-%m') if closing else '',
        'installment_no': None,
        'installment_count': None,
        'installment_total': None,
    }
    if installment:
        tx['installment_no'], tx['installment_count'], tx['installment_total'] = installment
    return tx


class BackfillTestCase(unittest.TestCase):
    def setUp(self):
        engine = create_engine('sqlite:///:memory:')
        Base.metadata.create_all(engine)
        self.db = sessionmaker(bind=engine)()
        user = User(email='owner@example.com', password_hash='x', is_admin=True, is_active=True)
        self.db.add(user)
        self.db.flush()
        self.uid = user.id
        tag = Tag(user_id=self.uid, name='grocery')
        self.db.add(tag)

        def expense(day, description, amount):
            # Imported under the old, one-month-late period.
            return Expense(user_id=self.uid, date=datetime.strptime(day, '%Y-%m-%d'),
                           description=description, amount=amount, card_number='1111',
                           statement_period='2026-06', bank_source='isbank',
                           category='Uncategorized')

        shop = expense('2026-04-20', 'ACME MARKET', 100.0)
        plan = expense('2026-04-22', 'ACME ELEKTRONIK', 300.0)
        orphan = expense('2026-04-23', 'NOT IN ANY PDF', 5.0)
        self.db.add_all([shop, plan, orphan])
        self.db.flush()
        self.db.add(ExpenseTag(expense_id=shop.id, tag_id=tag.id, source='manual'))
        self.db.add(ExpenseTag(expense_id=plan.id, tag_id=tag.id, source='auto'))
        self.db.commit()
        self.shop_id, self.plan_id, self.orphan_id = shop.id, plan.id, orphan.id

        self.files = {
            FIRST: [
                _tx('2026-04-20', 'ACME MARKET', 100.0),
                _tx('2026-04-22', 'ACME ELEKTRONIK', 300.0, installment=(1, 3, 900.0)),
                _tx('2026-04-25', 'ONLY IN THE PDF', 7.0),
            ],
            # The same statement delivered by a second email.
            RESENT: [_tx('2026-04-20', 'ACME MARKET', 100.0)],
        }

    def tearDown(self):
        self.db.close()

    def run_backfill(self, apply=True):
        return backfill_statements(
            self.db, self.uid, sorted(self.files), self.files.__getitem__, apply=apply)

    def tags(self):
        return sorted((t.expense_id, t.tag_id, t.source) for t in self.db.query(ExpenseTag))

    def test_updates_matched_rows_in_place(self):
        report = self.run_backfill()

        self.assertEqual(report.matched, 2)
        self.assertEqual(report.statements, 1)
        self.assertEqual(report.installments, 1)
        self.assertEqual(report.period_changes, Counter({('2026-06', '2026-05'): 2}))

        statement = self.db.query(Statement).one()
        self.assertEqual(
            (statement.card_number, statement.closing_date, statement.period, statement.email_id),
            ('1111', date(2026, 5, 5), '2026-05', 'MSG1'),
        )
        shop = self.db.get(Expense, self.shop_id)
        self.assertEqual((shop.statement_id, shop.statement_period), (statement.id, '2026-05'))
        plan = self.db.get(Expense, self.plan_id)
        self.assertEqual(
            (plan.installment_no, plan.installment_count, plan.installment_total), (1, 3, 900.0))

    def test_never_inserts_or_deletes_expenses(self):
        report = self.run_backfill()

        self.assertEqual(self.db.query(Expense).count(), 3)
        self.assertEqual(report.parsed_unmatched, [
            ('bank(isbank),date(2026-05-06),emailid(MSG1),card(1111).pdf',
             '2026-04-25', 'ONLY IN THE PDF', 7.0),
        ])
        self.assertEqual(report.db_unmatched, [self.orphan_id])
        self.assertIsNone(self.db.get(Expense, self.orphan_id).statement_id)

    def test_redelivered_statement_is_skipped_and_listed(self):
        report = self.run_backfill()
        self.assertEqual(report.duplicate_files, [
            ('bank(isbank),date(2026-05-07),emailid(MSG2),card(1111).pdf',
             'bank(isbank),date(2026-05-06),emailid(MSG1),card(1111).pdf'),
        ])

    def _add_usd_statement(self, stored_currency):
        """A USD statement for the same card and closing date as FIRST."""
        online = Expense(user_id=self.uid, date=datetime(2026, 4, 24), description='ACME ONLINE',
                         amount=10.0, currency=stored_currency, card_number='1111',
                         statement_period='2026-06', bank_source='isbank',
                         category='Uncategorized')
        self.db.add(online)
        self.db.commit()
        self.files[USD_FILE] = [_tx('2026-04-24', 'ACME ONLINE', 10.0, currency='USD')]
        return online.id

    def test_same_closing_date_in_another_currency_is_its_own_statement(self):
        online_id = self._add_usd_statement(stored_currency='USD')
        report = self.run_backfill()

        self.assertEqual(report.matched, 3)
        self.assertEqual(report.statements, 2)
        self.assertEqual([skipped for skipped, _ in report.duplicate_files],
                         ['bank(isbank),date(2026-05-07),emailid(MSG2),card(1111).pdf'])
        online = self.db.get(Expense, online_id)
        self.assertEqual(online.statement.currency, 'USD')
        self.assertEqual(self.db.get(Expense, self.shop_id).statement.currency, 'TRY')
        self.assertEqual(report.currency_changes, [])

    def test_corrects_a_currency_the_parser_now_reads_differently(self):
        online_id = self._add_usd_statement(stored_currency='TRY')
        report = self.run_backfill()

        self.assertEqual(self.db.get(Expense, online_id).currency, 'USD')
        self.assertEqual(report.currency_changes, [(online_id, 'TRY', 'USD')])

    def test_tags_are_untouched(self):
        before = self.tags()
        self.run_backfill()
        self.assertEqual(self.tags(), before)

    def test_dry_run_writes_nothing(self):
        report = self.run_backfill(apply=False)

        self.assertEqual(report.matched, 2)
        self.assertEqual(self.db.query(Statement).count(), 0)
        self.assertEqual(self.db.get(Expense, self.shop_id).statement_period, '2026-06')
        self.assertIsNone(self.db.get(Expense, self.plan_id).installment_count)

    def test_second_run_changes_nothing(self):
        self.run_backfill()
        report = self.run_backfill()

        self.assertEqual(report.matched, 2)
        self.assertEqual(report.period_changes, Counter())
        self.assertEqual(self.db.query(Statement).count(), 1)

    def test_file_without_closing_date_is_listed_not_used(self):
        self.files = {FIRST: [_tx('2026-04-20', 'ACME MARKET', 100.0, closing=None)]}
        report = self.run_backfill()

        self.assertEqual(report.files_without_closing_date,
                         ['bank(isbank),date(2026-05-06),emailid(MSG1),card(1111).pdf'])
        self.assertEqual(report.matched, 0)

    def test_aborts_and_rolls_back_when_a_tag_set_changes(self):
        real_snapshot = src.backfill.tag_snapshot
        calls = []

        def drifting_snapshot(db, user_id):
            snapshot = real_snapshot(db, user_id)
            calls.append(1)
            return snapshot if len(calls) == 1 else {**snapshot, -1: ((0, 'auto'),)}

        with patch('src.backfill.tag_snapshot', side_effect=drifting_snapshot):
            with self.assertRaises(TagSetChanged):
                self.run_backfill()

        self.assertEqual(self.db.query(Statement).count(), 0)
        self.assertEqual(self.db.get(Expense, self.shop_id).statement_period, '2026-06')


if __name__ == '__main__':
    unittest.main()
