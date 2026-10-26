"""Schema for statements, installment columns and rates (see the insights foundation spec)."""

import unittest
from datetime import date, datetime

from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from src.models import Base, Expense, Rate, Statement, User


class StatementModelTestCase(unittest.TestCase):
    def setUp(self):
        engine = create_engine('sqlite:///:memory:')
        Base.metadata.create_all(engine)
        self.db = sessionmaker(bind=engine)()
        user = User(email='owner@example.com', password_hash='x', is_admin=True, is_active=True)
        self.db.add(user)
        self.db.commit()
        self.uid = user.id

    def tearDown(self):
        self.db.close()

    def _statement(self, **overrides):
        fields = dict(user_id=self.uid, card_number='1111', closing_date=date(2026, 5, 5),
                      period='2026-05', source_file='a.pdf', email_id='MSG1')
        fields.update(overrides)
        return Statement(**fields)

    def test_expense_links_to_statement_and_carries_installments(self):
        statement = self._statement()
        self.db.add(Expense(
            user_id=self.uid, date=datetime(2026, 4, 20), description='ACME ELEKTRONIK',
            amount=300.0, bank_source='isbank', statement=statement,
            installment_no=1, installment_count=3, installment_total=900.0,
        ))
        self.db.commit()

        expense = self.db.query(Expense).one()
        self.assertEqual(expense.statement.period, '2026-05')
        self.assertEqual(expense.statement.closing_date, date(2026, 5, 5))
        self.assertEqual(
            (expense.installment_no, expense.installment_count, expense.installment_total),
            (1, 3, 900.0),
        )
        self.assertEqual([e.id for e in statement.expenses], [expense.id])

    def test_installment_columns_default_to_null(self):
        self.db.add(Expense(user_id=self.uid, date=datetime(2026, 4, 20),
                            description='ACME', amount=1.0, bank_source='isbank'))
        self.db.commit()
        expense = self.db.query(Expense).one()
        self.assertIsNone(expense.statement_id)
        self.assertIsNone(expense.installment_count)

    def test_one_statement_per_user_card_closing_date_and_currency(self):
        self.db.add(self._statement())
        self.db.add(self._statement(source_file='b.pdf', email_id='MSG2'))
        with self.assertRaises(IntegrityError):
            self.db.commit()

    def test_try_and_usd_statements_share_card_and_closing_date(self):
        # İşbank sends one statement per currency for the same card and cycle.
        self.db.add(self._statement(currency='TRY'))
        self.db.add(self._statement(currency='USD', source_file='b.pdf'))
        self.db.commit()
        self.assertEqual(
            sorted(s.currency for s in self.db.query(Statement)), ['TRY', 'USD'])

    def test_rate_is_keyed_by_series_and_month(self):
        self.db.merge(Rate(series='USDTRY', month='2026-05', value=40.0, provisional=True))
        self.db.merge(Rate(series='USDTRY', month='2026-05', value=41.0, provisional=False))
        self.db.commit()
        rate = self.db.query(Rate).one()
        self.assertEqual((rate.value, rate.provisional), (41.0, False))


if __name__ == '__main__':
    unittest.main()
