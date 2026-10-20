import unittest
from datetime import date, datetime

from src.analytics.months import add_months, month_key, month_range


class MonthsTestCase(unittest.TestCase):
    def test_month_key(self):
        self.assertEqual(month_key(date(2026, 4, 7)), '2026-04')
        self.assertEqual(month_key(datetime(2019, 12, 31, 23, 0)), '2019-12')

    def test_add_months_forward_backward_and_across_years(self):
        self.assertEqual(add_months(date(2026, 1, 15), 1), date(2026, 2, 15))
        self.assertEqual(add_months(date(2026, 1, 15), -1), date(2025, 12, 15))
        self.assertEqual(add_months(date(2025, 11, 28), 3), date(2026, 2, 28))
        self.assertEqual(add_months(date(2026, 3, 6), -2), date(2026, 1, 6))

    def test_add_months_clamps_the_day(self):
        self.assertEqual(add_months(date(2026, 1, 31), 1), date(2026, 2, 28))
        self.assertEqual(add_months(date(2024, 3, 31), -1), date(2024, 2, 29))

    def test_add_months_keeps_datetime(self):
        self.assertEqual(add_months(datetime(2026, 1, 31, 9, 30), 1), datetime(2026, 2, 28, 9, 30))

    def test_month_range_is_inclusive(self):
        self.assertEqual(month_range('2025-11', '2026-02'),
                         ['2025-11', '2025-12', '2026-01', '2026-02'])
        self.assertEqual(month_range('2026-04', '2026-04'), ['2026-04'])
        self.assertEqual(month_range('2026-05', '2026-04'), [])


if __name__ == '__main__':
    unittest.main()
