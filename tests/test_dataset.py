"""The clean spend dataset (src/analytics/dataset.py). Synthetic data only."""

import unittest
from datetime import date, datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.analytics.dataset import BILLED, PURCHASED, load_spend
from src.analytics.rates import USDTRY, RateTable
from src.models import Base, Expense, ExpenseTag, Tag, User

HIERARCHY = {
    'food': ['grocery'],
    'money': ['payment', 'cashback', 'refund', 'fee'],
    'shopping': ['electronics'],
    'leisure': ['sports'],
}


class LoadSpendTestCase(unittest.TestCase):
    def setUp(self):
        engine = create_engine('sqlite:///:memory:')
        Base.metadata.create_all(engine)
        self.db = sessionmaker(bind=engine)()
        self.uid = self._user('a@example.com')
        self.other_uid = self._user('b@example.com')

        self.tags = {}
        for parent, children in HIERARCHY.items():
            parent_tag = Tag(user_id=self.uid, name=parent)
            self.db.add(parent_tag)
            self.db.flush()
            self.tags[parent] = parent_tag
            for child in children:
                child_tag = Tag(user_id=self.uid, name=child, parent_id=parent_tag.id)
                self.db.add(child_tag)
                self.db.flush()
                self.tags[child] = child_tag

        self.rates = RateTable({USDTRY: {'2026-01': 30.0}})
        add = self._expense
        self.grocery = add('2026-01-10', 'ACME MARKET ISTANBUL TR', 100.0, child='grocery')
        add('2026-01-11', 'HESAPTAN AKTARIM', -500.0, child='payment', category='Payment')
        add('2026-01-12', 'OTOMATIK TAHSILAT', -50.0, child='payment')
        add('2026-01-13', 'ACME BONUS', -5.0, child='cashback')
        self.refund = add('2026-01-14', 'İADE/ACME OYUNCAK', -20.0, child='refund')
        self.usd = add('2026-01-15', 'ACME ONLINE', 10.0, child='electronics', currency='USD')
        add('2026-02-15', 'ACME ONLINE', 10.0, child='electronics', currency='USD')  # no Feb rate
        # Point-of-sale plan: 3 × 300 of a 900 purchase.
        self.pos = [
            add(day, 'ACME ELEKTRONIK ISTANBUL TR', 300.0, child='electronics',
                installment=(n, 3, 900.0))
            for n, day in enumerate(('2026-01-06', '2026-02-06', '2026-03-06'), start=1)
        ]
        # Post-purchase conversion: purchase, reversal, then installments
        # (the third one's statement is missing).
        self.converted = add('2026-01-20', 'ACME SPOR ISTANBUL TR', 600.0, child='sports')
        self.reversal = add('2026-01-22', 'ACME SPOR ISTANBUL TR İŞLEM İPTALI', -600.0, child='sports')
        self.conversion = [
            add(day, 'TAKSİTLENDİRME/ACME SPOR ISTANBUL TR', 200.0, child='sports',
                installment=(n, 3, 600.0))
            for n, day in enumerate(('2026-01-28', '2026-02-28'), start=1)
        ]
        # Looks like an installment but carries no installment columns.
        self.unparsed = add('2026-03-28', 'TAKSİTLENDİRME/ACME DIGER ISTANBUL TR', 50.0)
        self._expense('2026-01-10', 'ACME MARKET', 999.0, user_id=self.other_uid)
        self.db.commit()

    def tearDown(self):
        self.db.close()

    def _user(self, email):
        user = User(email=email, password_hash='x', is_active=True)
        self.db.add(user)
        self.db.flush()
        return user.id

    def _expense(self, day, description, amount, *, child=None, category='Uncategorized',
                 currency='TRY', installment=None, user_id=None):
        expense = Expense(
            user_id=user_id or self.uid, date=datetime.strptime(day, '%Y-%m-%d'),
            description=description, amount=amount, currency=currency, category=category,
            bank_source='isbank', card_number='1111',
        )
        if installment:
            expense.installment_no, expense.installment_count, expense.installment_total = installment
        self.db.add(expense)
        self.db.flush()
        if child:
            child_tag = self.tags[child]
            self.db.add(ExpenseTag(expense_id=expense.id, tag_id=child_tag.parent_id, source='auto'))
            self.db.add(ExpenseTag(expense_id=expense.id, tag_id=child_tag.id, source='auto'))
        return expense.id

    def load(self, basis=BILLED, **kwargs):
        return load_spend(self.db, self.uid, basis=basis, rates=self.rates, **kwargs)

    def test_billed_keeps_every_spend_row_as_posted(self):
        spend = self.load()

        self.assertEqual(
            sorted(r.expense_id for r in spend.rows),
            sorted([self.grocery, self.refund, self.usd, *self.pos, self.converted,
                    self.reversal, *self.conversion, self.unparsed]),
        )
        self.assertAlmostEqual(sum(r.amount_try for r in spend.rows), 1730.0)

    def test_payments_and_cashback_are_excluded(self):
        descriptions = {r.merchant for r in self.load().rows}
        self.assertNotIn('HESAPTAN AKTARIM', descriptions)
        self.assertNotIn('OTOMATIK TAHSILAT', descriptions)   # payment tag, not category
        self.assertNotIn('ACME BONUS', descriptions)

    def test_row_fields(self):
        rows = {r.expense_id: r for r in self.load().rows}

        grocery = rows[self.grocery]
        self.assertEqual((grocery.date, grocery.month), (date(2026, 1, 10), '2026-01'))
        self.assertEqual((grocery.parent_tag, grocery.child_tag), ('food', 'grocery'))
        self.assertEqual((grocery.merchant, grocery.card, grocery.kind),
                         ('ACME MARKET', '1111', 'purchase'))
        self.assertEqual(rows[self.refund].kind, 'refund')
        self.assertEqual(rows[self.reversal].kind, 'cancel')
        self.assertEqual(rows[self.pos[0]].kind, 'installment')
        self.assertEqual(rows[self.conversion[0]].merchant, 'ACME SPOR')
        self.assertEqual(rows[self.unparsed].parent_tag, None)

    def test_foreign_currency_converts_or_is_dropped(self):
        spend = self.load()
        rows = {r.expense_id: r for r in spend.rows}
        self.assertEqual(rows[self.usd].amount_try, 300.0)
        self.assertEqual(spend.dropped_no_rate, 1)

    def test_without_rates_foreign_rows_are_dropped(self):
        spend = load_spend(self.db, self.uid)
        self.assertNotIn(self.usd, {r.expense_id for r in spend.rows})
        self.assertEqual(spend.dropped_no_rate, 2)

    def test_purchased_collapses_each_plan_into_one_purchase(self):
        spend = self.load(PURCHASED)
        rows = {r.expense_id: r for r in spend.rows}

        pos = rows[self.pos[0]]
        self.assertEqual((pos.date, pos.amount_try, pos.kind), (date(2026, 1, 6), 900.0, 'purchase'))
        self.assertNotIn(self.pos[1], rows)
        self.assertNotIn(self.pos[2], rows)

        plan = rows[self.conversion[0]]
        self.assertEqual((plan.date, plan.amount_try), (date(2026, 1, 28), 600.0))
        self.assertNotIn(self.conversion[1], rows)
        # The original purchase and its reversal stay and cancel out.
        self.assertEqual(rows[self.converted].amount_try + rows[self.reversal].amount_try, 0.0)

        self.assertAlmostEqual(sum(r.amount_try for r in spend.rows), 1930.0)

    def test_plan_with_missing_first_installment_is_dated_back(self):
        for n, day in ((2, '2026-05-10'), (3, '2026-06-10')):
            self._expense(day, 'ACME MOBILYA', 100.0, installment=(n, 3, 300.0))
        self.db.commit()

        rows = [r for r in self.load(PURCHASED).rows if r.merchant == 'ACME MOBILYA']

        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0].date, rows[0].amount_try), (date(2026, 4, 10), 300.0))

    def test_installment_marker_without_columns_counts_as_fallback(self):
        self.assertEqual(self.load().fallbacks, 1)
        self.assertEqual(self.load(PURCHASED).fallbacks, 1)

    def test_rows_are_sorted_by_date(self):
        rows = self.load().rows
        self.assertEqual(rows, sorted(rows, key=lambda r: (r.date, r.expense_id)))

    def test_only_the_users_rows(self):
        spend = load_spend(self.db, self.other_uid)
        self.assertEqual([r.amount_try for r in spend.rows], [999.0])

    def test_unknown_basis_is_rejected(self):
        with self.assertRaises(ValueError):
            self.load('as-dreamed')


if __name__ == '__main__':
    unittest.main()
