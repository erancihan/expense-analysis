"""Unit tests for the İşbank statement parser's pure functions.

The full layout walk needs real PDFs, but the bug-prone logic — Turkish number
normalisation, amount/date pattern matching, card-metadata extraction and
position matching — is covered here without any PDF fixtures.
"""

import unittest
from datetime import date

from src.parsers.isbank import (
    _normalise_amount,
    _extract_amount_values,
    _extract_card_metadata,
    _find_closing_date,
    _parse_installment,
    AMOUNT_PATTERN,
    DATE_PATTERN,
    IsbankParser,
)


class NormaliseAmountTestCase(unittest.TestCase):
    def test_turkish_comma_decimal(self):
        self.assertEqual(_normalise_amount('1.234,56'), 1234.56)
        self.assertEqual(_normalise_amount('22.926,40'), 22926.40)
        self.assertEqual(_normalise_amount('0,00'), 0.0)

    def test_dot_decimal_usd_style(self):
        self.assertEqual(_normalise_amount('579.09'), 579.09)
        self.assertEqual(_normalise_amount('0.00'), 0.0)

    def test_negative_with_space_after_sign(self):
        self.assertEqual(_normalise_amount('- 65.195,00'), -65195.0)
        self.assertEqual(_normalise_amount('-1.000,50'), -1000.5)

    def test_leading_plus_is_positive(self):
        self.assertEqual(_normalise_amount('+1.000,50'), 1000.5)

    def test_invalid_returns_none(self):
        self.assertIsNone(_normalise_amount('abc'))
        self.assertIsNone(_normalise_amount(''))


class AmountPatternTestCase(unittest.TestCase):
    def test_matches_valid_amounts(self):
        for s in ('1.234,56', '- 65.195,00', '579.09', '0.00', '+12,00'):
            self.assertTrue(AMOUNT_PATTERN.match(s), s)

    def test_rejects_non_amounts(self):
        for s in ('TUTAR', '100', '1.234,5', '12,345', 'MIGROS'):
            self.assertIsNone(AMOUNT_PATTERN.match(s), s)


class DatePatternTestCase(unittest.TestCase):
    def test_splits_date_and_description(self):
        m = DATE_PATTERN.match('07/04/2026 MIGROS MARKET')
        self.assertEqual(m.group(1), '07/04/2026')
        self.assertEqual(m.group(2), 'MIGROS MARKET')

    def test_handles_no_space_after_date(self):
        m = DATE_PATTERN.match('07/04/2026MIGROS')
        self.assertEqual(m.group(1), '07/04/2026')
        self.assertEqual(m.group(2), 'MIGROS')

    def test_non_date_line_does_not_match(self):
        self.assertIsNone(DATE_PATTERN.match('MIGROS 07/04/2026'))


class ExtractAmountValuesTestCase(unittest.TestCase):
    def test_skips_currency_labelled_summary_lines(self):
        text = "TUTAR\n1.234,56\n22.926,40 TL\n709,85 USD\n579.09"
        # The "… TL"/"… USD" lines are balances, not transaction amounts.
        self.assertEqual(_extract_amount_values(text), [1234.56, 579.09])


class CardMetadataTestCase(unittest.TestCase):
    def test_extracts_card_number_type_period_default_try(self):
        text = "\n".join([
            "SN. AD SOYAD",
            "4543********6152",
            "VISA Klasik Hesap Özetiniz",
            "Hesap Kesim Tarihi",
            "07.04.2026",
            "Hesap Özeti Borcu",
            "1.234,56 TL",
        ])
        meta = _extract_card_metadata(text)
        self.assertEqual(meta['card_number'], '4543********6152')
        self.assertEqual(meta['card_type'], 'VISA Klasik')
        self.assertEqual(meta['statement_period'], '2026-04')
        self.assertEqual(meta['statement_currency'], 'TRY')

    def test_detects_foreign_statement_currency(self):
        text = "\n".join([
            "1234********9999",
            "MAXIMUM Hesap Özetiniz",
            "Hesap Özeti Borcu",
            "709,85 USD",
        ])
        meta = _extract_card_metadata(text)
        self.assertEqual(meta['statement_currency'], 'USD')

    def test_detects_foreign_currency_with_a_signed_balance(self):
        # A credit balance prints with a leading '+', and the minimum payment
        # line as '+*0,00 USD'. Missing these filed a USD statement as TRY.
        text = "\n".join([
            "1234********9999",
            "VISA Hesap Özetiniz",
            "+11,58 USD",
            "Hesap Özeti Borcu:",
            "Ödenmesi Gereken Asgari Tutar: +*0,00 USD",
            "153,75 TL",
        ])
        meta = _extract_card_metadata(text)
        self.assertEqual(meta['statement_currency'], 'USD')


class FindAmountAtPositionTestCase(unittest.TestCase):
    def setUp(self):
        self.parser = IsbankParser()

    def test_matches_within_tolerance(self):
        amount_map = {100.0: 50.0, 200.0: 75.0}
        self.assertEqual(self.parser._find_amount_at_position(101.0, amount_map), 50.0)
        self.assertEqual(self.parser._find_amount_at_position(199.0, amount_map), 75.0)

    def test_returns_none_outside_tolerance(self):
        amount_map = {100.0: 50.0}
        self.assertIsNone(self.parser._find_amount_at_position(110.0, amount_map))


class FindClosingDateTestCase(unittest.TestCase):
    """raw_lines entries are (x0, x1, top, text), as built in _parse_with_layout."""

    def test_pairs_label_with_value_on_its_row_not_first_date_in_text(self):
        raw_lines = [
            # pdfminer emitted the *next* closing date first in the 2021 layout.
            (454.0, 499.1, 191.0, '05.12.2021'),
            (308.4, 429.9, 191.0, 'Bir Sonraki Hesap Kesim Tarihi:'),
            (308.4, 384.3, 144.8, 'Hesap Kesim Tarihi:'),
            (454.0, 499.1, 144.8, '05.11.2021'),
            (308.4, 385.9, 156.9, 'Son Ödeme Tarihi:'),
            (454.0, 499.1, 156.6, '15.11.2021'),
        ]
        self.assertEqual(_find_closing_date(raw_lines, 3.0), date(2021, 11, 5))

    def test_value_half_a_point_off_the_label_row_still_pairs(self):
        raw_lines = [
            (311.9, 396.2, 137.8, 'Hesap Kesim Tarihi:'),
            (465.0, 508.6, 138.3, '28.05.2023'),
            (465.0, 508.6, 149.8, '07.06.2023'),
        ]
        self.assertEqual(_find_closing_date(raw_lines, 3.0), date(2023, 5, 28))

    def test_value_inline_with_label(self):
        raw_lines = [(308.0, 480.0, 100.0, 'Hesap Kesim Tarihi: 07.04.2026')]
        self.assertEqual(_find_closing_date(raw_lines, 3.0), date(2026, 4, 7))

    def test_value_on_another_row_is_not_used(self):
        raw_lines = [
            (308.0, 384.0, 100.0, 'Hesap Kesim Tarihi:'),
            (454.0, 499.0, 120.0, '07.04.2026'),
        ]
        self.assertIsNone(_find_closing_date(raw_lines, 3.0))

    def test_no_label_returns_none(self):
        self.assertIsNone(_find_closing_date([(454.0, 499.0, 100.0, '07.04.2026')], 3.0))


class ParseInstallmentTestCase(unittest.TestCase):
    def test_parses_number_count_and_total(self):
        self.assertEqual(_parse_installment('2/4 taksidi (11.323,24)'), (2, 4, 11323.24))

    def test_small_total(self):
        self.assertEqual(_parse_installment('1/3 taksidi (997,50)'), (1, 3, 997.50))

    def test_other_text_is_none(self):
        self.assertIsNone(_parse_installment('TAKSİT BİLGİSİ MAXIPUAN'))
        self.assertIsNone(_parse_installment('Artı Taksit'))


if __name__ == '__main__':
    unittest.main()
