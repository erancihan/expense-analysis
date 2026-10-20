"""Which months the imported statements fully cover (src/analytics/coverage.py)."""

import unittest
from datetime import date, datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.analytics.coverage import COMPLETE, INCOMPLETE, NONE, compute_coverage, coverage
from src.models import Base, Expense, Statement, User


def monthly(card_day, *months):
    """Closing dates on `card_day` of each 'YYYY-MM' month."""
    return [date(int(m[:4]), int(m[5:]), card_day) for m in months]


class ComputeCoverageTestCase(unittest.TestCase):
    def test_every_statement_present_is_complete(self):
        result = compute_coverage(
            {'1111': monthly(15, '2026-01', '2026-02', '2026-03', '2026-04')},
            '2026-01', '2026-04')
        self.assertEqual(set(result.values()), {COMPLETE})

    def test_missing_statement_marks_the_months_it_covered(self):
        # The 15 March statement is missing: it held 16 Feb – 15 Mar.
        result = compute_coverage(
            {'1111': monthly(15, '2026-01', '2026-02', '2026-04')},
            '2026-01', '2026-04')
        self.assertEqual(result, {
            '2026-01': COMPLETE, '2026-02': INCOMPLETE,
            '2026-03': INCOMPLETE, '2026-04': COMPLETE,
        })

    def test_gap_between_cards_is_incomplete(self):
        # Card A stops after March; card B's first statement covers only 29 Apr on.
        result = compute_coverage(
            {'AAAA': monthly(28, '2026-01', '2026-02', '2026-03'),
             'BBBB': monthly(28, '2026-05', '2026-06')},
            '2026-01', '2026-06')
        self.assertEqual(result['2026-03'], COMPLETE)     # only 29–31 Mar uncovered
        self.assertEqual(result['2026-04'], INCOMPLETE)
        self.assertEqual(result['2026-05'], COMPLETE)

    def test_one_card_missing_a_statement_is_enough(self):
        result = compute_coverage(
            {'AAAA': monthly(15, '2026-01', '2026-02', '2026-03', '2026-04'),
             'BBBB': monthly(15, '2026-01', '2026-02', '2026-04')},
            '2026-01', '2026-04')
        self.assertEqual(result['2026-02'], INCOMPLETE)
        self.assertEqual(result['2026-03'], INCOMPLETE)

    def test_months_before_any_card_are_none(self):
        result = compute_coverage({'1111': monthly(15, '2026-01')}, '2025-10', '2026-01')
        self.assertEqual(result, {
            '2025-10': NONE, '2025-11': NONE,
            '2025-12': COMPLETE,   # 16–31 Dec covered; the card did not exist before
            '2026-01': COMPLETE,
        })

    def test_closing_date_drift_of_two_days_is_ignored(self):
        closings = [date(2026, 1, 28), date(2026, 2, 28), date(2026, 3, 30), date(2026, 4, 28)]
        result = compute_coverage({'1111': closings}, '2026-01', '2026-04')
        self.assertEqual(set(result.values()), {COMPLETE})

    def test_no_statements_at_all(self):
        self.assertEqual(compute_coverage({}, '2026-01', '2026-02'),
                         {'2026-01': NONE, '2026-02': NONE})


class CoverageFromDatabaseTestCase(unittest.TestCase):
    def setUp(self):
        engine = create_engine('sqlite:///:memory:')
        Base.metadata.create_all(engine)
        self.db = sessionmaker(bind=engine)()
        self.users = []
        for email in ('a@example.com', 'b@example.com'):
            user = User(email=email, password_hash='x', is_active=True)
            self.db.add(user)
            self.db.flush()
            self.users.append(user.id)

    def tearDown(self):
        self.db.close()

    def test_uses_only_the_users_statements_and_expense_range(self):
        a, b = self.users
        for closing in monthly(15, '2026-01', '2026-02', '2026-04'):
            self.db.add(Statement(user_id=a, card_number='1111', closing_date=closing,
                                  period=closing.strftime('%Y-%m')))
        # User b has the March statement; it must not fill user a's gap.
        self.db.add(Statement(user_id=b, card_number='1111', closing_date=date(2026, 3, 15),
                              period='2026-03'))
        for day in (datetime(2026, 1, 3), datetime(2026, 4, 10)):
            self.db.add(Expense(user_id=a, date=day, description='ACME', amount=1.0,
                                bank_source='isbank'))
        self.db.commit()

        result = coverage(self.db, a)

        self.assertEqual(list(result), ['2026-01', '2026-02', '2026-03', '2026-04'])
        self.assertEqual(result['2026-03'], INCOMPLETE)

    def test_user_without_expenses_gets_empty_map(self):
        self.assertEqual(coverage(self.db, self.users[0]), {})


if __name__ == '__main__':
    unittest.main()
