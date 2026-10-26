"""Trend maths (src/analytics/trend.py). Synthetic data only."""

import unittest
from datetime import date

from src.analytics.coverage import COMPLETE, INCOMPLETE, NONE
from src.analytics.dataset import SpendRow
from src.analytics.rates import CPI_TR, USDTRY, RateTable
from src.analytics.trend import (
    LensRow, UNTAGGED, annual_totals, build_trend, kpis, lens_available,
    monthly_totals, paired_months, pct_change, quarter_key, quarterly_mix,
    rolling_average, to_lens, yoy_by_parent,
)


def spend(month, amount, parent='food', child='grocery', expense_id=1):
    day = date(int(month[:4]), int(month[5:]), 15)
    return SpendRow(expense_id=expense_id, date=day, month=month, amount_try=amount,
                    parent_tag=parent, child_tag=child, merchant='ACME', card='1234',
                    kind='purchase')


def lens(month, amount, parent='food', child='grocery'):
    return LensRow(month=month, parent=parent, child=child, amount=amount)


class LensTestCase(unittest.TestCase):
    def setUp(self):
        self.rates = RateTable({
            USDTRY: {'2026-01': 30.0, '2026-02': 32.0},
            CPI_TR: {'2026-01': 100.0, '2026-02': 110.0},
        })

    def test_nominal_keeps_try_amounts(self):
        rows, dropped = to_lens([spend('2026-01', 300.0)], 'nominal', self.rates)
        self.assertEqual([r.amount for r in rows], [300.0])
        self.assertEqual(dropped, 0)

    def test_real_rebases_to_latest_cpi_month(self):
        rows, _ = to_lens([spend('2026-01', 100.0)], 'real', self.rates)
        self.assertAlmostEqual(rows[0].amount, 110.0)

    def test_usd_divides_by_that_months_rate(self):
        rows, _ = to_lens([spend('2026-02', 64.0)], 'usd', self.rates)
        self.assertAlmostEqual(rows[0].amount, 2.0)

    def test_row_without_a_rate_is_dropped_and_counted(self):
        rows, dropped = to_lens([spend('2026-03', 10.0), spend('2026-01', 30.0)], 'usd', self.rates)
        self.assertEqual([r.month for r in rows], ['2026-01'])
        self.assertEqual(dropped, 1)

    def test_untagged_row_gets_the_untagged_parent(self):
        rows, _ = to_lens([spend('2026-01', 1.0, parent=None, child=None)], 'nominal', self.rates)
        self.assertEqual(rows[0].parent, UNTAGGED)

    def test_unknown_lens_raises(self):
        with self.assertRaises(ValueError):
            to_lens([], 'gold', self.rates)

    def test_availability_follows_the_rates_table(self):
        self.assertTrue(lens_available('nominal', RateTable({})))
        self.assertFalse(lens_available('real', RateTable({})))
        self.assertFalse(lens_available('usd', RateTable({})))
        self.assertTrue(lens_available('real', self.rates))
        self.assertTrue(lens_available('usd', self.rates))


class MonthlyTotalsTestCase(unittest.TestCase):
    def test_none_month_is_null_covered_empty_month_is_zero(self):
        coverage = {'2026-01': COMPLETE, '2026-02': NONE, '2026-03': INCOMPLETE, '2026-04': COMPLETE}
        rows = [lens('2026-01', 10.0), lens('2026-01', 5.0), lens('2026-02', 99.0), lens('2026-03', 7.0)]
        self.assertEqual(monthly_totals(rows, coverage), [
            {'month': '2026-01', 'total': 15.0, 'coverage': COMPLETE},
            {'month': '2026-02', 'total': None, 'coverage': NONE},
            {'month': '2026-03', 'total': 7.0, 'coverage': INCOMPLETE},
            {'month': '2026-04', 'total': 0.0, 'coverage': COMPLETE},
        ])

    def test_row_month_outside_coverage_extends_the_axis_as_none(self):
        result = monthly_totals([lens('2025-12', 1.0)], {'2026-01': COMPLETE})
        self.assertEqual([(m['month'], m['coverage']) for m in result],
                         [('2025-12', NONE), ('2026-01', COMPLETE)])

    def test_empty_inputs_give_empty_axis(self):
        self.assertEqual(monthly_totals([], {}), [])


class RollingAverageTestCase(unittest.TestCase):
    def _monthly(self, spec):
        return [{'month': f'2026-{i + 1:02d}', 'total': total, 'coverage': state}
                for i, (total, state) in enumerate(spec)]

    def test_window_averages_only_complete_months(self):
        monthly = self._monthly([(10, COMPLETE), (20, COMPLETE), (99, INCOMPLETE), (30, COMPLETE)])
        self.assertEqual(rolling_average(monthly, 3), [None, None, 15.0, 25.0])

    def test_window_needs_half_its_months_complete(self):
        monthly = self._monthly([(10, COMPLETE), (None, NONE), (99, INCOMPLETE), (30, COMPLETE)])
        # window 3 over months 1-3: one complete of three → None; months 2-4: one of three → None
        self.assertEqual(rolling_average(monthly, 3), [None, None, None, None])

    def test_short_series_is_all_none_for_a_12_month_window(self):
        monthly = self._monthly([(10, COMPLETE)] * 5)
        self.assertEqual(rolling_average(monthly, 12), [None] * 5)


def _coverage(first, last, incomplete=(), none=()):
    from src.analytics.months import month_range
    return {m: (NONE if m in none else INCOMPLETE if m in incomplete else COMPLETE)
            for m in month_range(first, last)}


class AnnualTotalsTestCase(unittest.TestCase):
    def test_sums_non_null_months_and_flags_the_last_year_as_ytd(self):
        monthly = [
            {'month': '2025-11', 'total': 10.0, 'coverage': COMPLETE},
            {'month': '2025-12', 'total': None, 'coverage': NONE},
            {'month': '2026-01', 'total': 5.0, 'coverage': INCOMPLETE},
            {'month': '2026-02', 'total': 7.0, 'coverage': COMPLETE},
        ]
        self.assertEqual(annual_totals(monthly, '2026-02'), [
            {'year': 2025, 'total': 10.0, 'months_covered': 1, 'is_ytd': False},
            {'year': 2026, 'total': 12.0, 'months_covered': 1, 'is_ytd': True},
        ])


class QuarterlyMixTestCase(unittest.TestCase):
    def test_shares_per_parent_sum_to_one(self):
        rows = [lens('2026-01', 30.0, 'food'), lens('2026-02', 10.0, 'shopping'),
                lens('2026-03', 10.0, None), lens('2026-04', 5.0, 'food')]
        self.assertEqual(quarter_key('2026-05'), '2026-Q2')
        mix = quarterly_mix(rows, '2026-01', '2026-04')
        self.assertEqual([q['quarter'] for q in mix], ['2026-Q1', '2026-Q2'])
        self.assertEqual(mix[0]['by_parent'], {'food': 0.6, 'shopping': 0.2, UNTAGGED: 0.2})
        self.assertEqual(mix[1]['by_parent'], {'food': 1.0})
        self.assertEqual(mix[0]['by_child'], {})

    def test_requested_parent_gets_child_shares(self):
        rows = [lens('2026-01', 30.0, 'food', 'grocery'), lens('2026-01', 10.0, 'food', None),
                lens('2026-01', 60.0, 'shopping', 'electronics')]
        mix = quarterly_mix(rows, '2026-01', '2026-03', parent='food')
        self.assertEqual(mix[0]['by_child'], {'grocery': 0.75, 'other': 0.25})

    def test_non_positive_quarter_has_no_shares(self):
        rows = [lens('2026-01', 30.0, 'food'), lens('2026-02', -30.0, 'food')]
        self.assertEqual(quarterly_mix(rows, '2026-01', '2026-03')[0]['by_parent'], {})

    def test_quarter_without_rows_is_still_listed(self):
        self.assertEqual([q['by_parent'] for q in quarterly_mix([], '2026-01', '2026-06')], [{}, {}])


class PairedComparisonTestCase(unittest.TestCase):
    def test_pair_is_dropped_when_either_month_is_not_complete(self):
        coverage = _coverage('2025-01', '2026-03', incomplete=('2025-02',), none=('2026-03',))
        pairs = paired_months(['2026-01', '2026-02', '2026-03'], coverage)
        self.assertEqual(pairs, [('2026-01', '2025-01')])

    def test_pct_change(self):
        self.assertAlmostEqual(pct_change(120.0, 100.0), 20.0)
        self.assertIsNone(pct_change(5.0, 0.0))
        self.assertIsNone(pct_change(5.0, -1.0))

    def test_yoy_by_parent_sorted_by_absolute_change_none_last(self):
        coverage = _coverage('2024-01', '2026-03')
        rows = [lens('2025-03', 100.0, 'food'), lens('2026-03', 150.0, 'food'),
                lens('2025-02', 100.0, 'shopping'), lens('2026-02', 20.0, 'shopping'),
                lens('2026-01', 10.0, 'leisure')]
        result = yoy_by_parent(rows, coverage, '2026-03')
        self.assertEqual([r['parent'] for r in result], ['shopping', 'food', 'leisure'])
        self.assertEqual(result[0], {'parent': 'shopping', 'last_12': 20.0, 'prior_12': 100.0, 'delta_pct': -80.0})
        self.assertIsNone(result[2]['delta_pct'])

    def test_yoy_ignores_months_removed_from_either_window(self):
        coverage = _coverage('2024-01', '2026-03', none=('2025-03',))
        rows = [lens('2025-03', 100.0, 'food'), lens('2026-03', 150.0, 'food'),
                lens('2025-04', 10.0, 'food'), lens('2026-04', 10.0, 'food')]
        result = yoy_by_parent(rows, coverage, '2026-03')
        # 2026-03 pairs with 2025-03 (none) → both dropped; 2026-04 is after last_month;
        # 2025-04 sits in the recent window and pairs with an empty 2024-04.
        self.assertEqual(result[0]['last_12'], 10.0)
        self.assertEqual(result[0]['prior_12'], 0.0)
        self.assertIsNone(result[0]['delta_pct'])


class KpisTestCase(unittest.TestCase):
    def test_short_history_gives_null_comparisons_without_errors(self):
        coverage = _coverage('2026-01', '2026-05')
        rows = [lens('2026-01', 10.0), lens('2026-05', 30.0)]
        monthly = monthly_totals(rows, coverage)
        result = kpis(rows, monthly, coverage, '2026-05')
        self.assertIsNone(result['last12_vs_prior12_pct'])
        self.assertIsNone(result['ytd_vs_prior_ytd_pct'])
        self.assertEqual(result['months_compared'], 0)
        self.assertAlmostEqual(result['avg_monthly'], 8.0)   # (10+0+0+0+30)/5

    def test_comparisons_use_only_paired_complete_months(self):
        coverage = _coverage('2025-01', '2026-02', incomplete=('2026-02',))
        rows = [lens('2025-01', 100.0), lens('2026-01', 150.0),
                lens('2025-02', 100.0), lens('2026-02', 500.0)]
        monthly = monthly_totals(rows, coverage)
        result = kpis(rows, monthly, coverage, '2026-02')
        self.assertAlmostEqual(result['last12_vs_prior12_pct'], 50.0)
        self.assertAlmostEqual(result['ytd_vs_prior_ytd_pct'], 50.0)
        self.assertEqual(result['months_compared'], 1)


class BuildTrendTestCase(unittest.TestCase):
    def test_payload_shape_and_range_slice(self):
        rates = RateTable({USDTRY: {m: 10.0 for m in _coverage('2025-01', '2026-03')}})
        coverage = _coverage('2025-01', '2026-03')
        rows = [spend('2025-01', 100.0), spend('2026-03', 200.0, parent='shopping', child=None)]
        payload = build_trend(rows, coverage, 'usd', rates, '2026-01', '2026-03')
        self.assertEqual(set(payload), {'monthly', 'rolling', 'annual', 'mix', 'yoy', 'kpis', 'dropped_no_rate'})
        self.assertEqual([m['month'] for m in payload['monthly']], ['2026-01', '2026-02', '2026-03'])
        self.assertEqual(payload['monthly'][2]['total'], 20.0)
        self.assertEqual(set(payload['rolling']), {'3', '12'})
        self.assertEqual(len(payload['rolling']['12']), 3)
        self.assertEqual(payload['annual'], [{'year': 2026, 'total': 20.0, 'months_covered': 3, 'is_ytd': True}])
        self.assertEqual([q['quarter'] for q in payload['mix']], ['2026-Q1'])
        self.assertEqual(payload['dropped_no_rate'], 0)

    def test_empty_history(self):
        payload = build_trend([], {}, 'nominal', RateTable({}), '', '')
        self.assertEqual(payload['monthly'], [])
        self.assertEqual(payload['annual'], [])
        self.assertEqual(payload['yoy'], [])
        self.assertIsNone(payload['kpis']['avg_monthly'])
