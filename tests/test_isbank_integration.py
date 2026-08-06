import os
import tempfile
import unittest
from datetime import datetime

from src.parsers.isbank import IsbankParser
from tests.fixtures.isbank_pdf import Row, build_statement


class IsbankIntegrationTest(unittest.TestCase):
    """End-to-end tests: a generated PDF through the real pdfminer layout path."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = os.path.join(tmp.name, 'statement.pdf')

    def parse(self, rows, **kwargs):
        build_statement(self.path, rows, **kwargs)
        return IsbankParser().extract_transactions(self.path)

    def test_extracts_transactions_and_card_metadata(self):
        txs = self.parse([
            Row('01/07/2026 MIGROS ISTANBUL', '1.234,56'),
            Row('02/07/2026 SHELL', '89,90'),
        ])

        self.assertEqual(len(txs), 2)

        self.assertEqual(txs[0]['date'], datetime(2026, 7, 1))
        self.assertEqual(txs[0]['description'], 'MIGROS ISTANBUL')
        self.assertEqual(txs[0]['amount'], 1234.56)
        self.assertEqual(txs[0]['category'], 'Uncategorized')

        self.assertEqual(txs[1]['date'], datetime(2026, 7, 2))
        self.assertEqual(txs[1]['description'], 'SHELL')
        self.assertEqual(txs[1]['amount'], 89.90)

        for tx in txs:
            self.assertEqual(tx['currency'], 'TRY')
            self.assertEqual(tx['card_number'], '4543********6152')
            self.assertEqual(tx['card_type'], 'VISA Klasik')
            self.assertEqual(tx['statement_period'], '2026-07')

    def test_row_without_tutar_value_gets_amount_none(self):
        txs = self.parse([
            Row('01/07/2026 MIGROS ISTANBUL', '1.234,56'),
            Row('02/07/2026 A101 MARKET'),
        ])

        self.assertEqual(len(txs), 2)
        self.assertEqual(txs[0]['amount'], 1234.56)
        self.assertIsNone(txs[1]['amount'])
        self.assertEqual(txs[1]['description'], 'A101 MARKET')

    def test_continuation_line_appends_to_description(self):
        txs = self.parse([
            Row('01/07/2026 MIGROS ISTANBUL', '1.234,56'),
            Row('DEVAM ACIKLAMA'),
        ])

        self.assertEqual(len(txs), 1)
        self.assertEqual(txs[0]['description'], 'MIGROS ISTANBUL DEVAM ACIKLAMA')
        self.assertEqual(txs[0]['amount'], 1234.56)

    def test_total_marker_stops_parsing(self):
        txs = self.parse([
            Row('01/07/2026 MIGROS ISTANBUL', '1.234,56'),
            Row('TOPLAM'),
            Row('02/07/2026 AFTER TOPLAM', '99,99'),
        ])

        self.assertEqual(len(txs), 1)
        self.assertEqual(txs[0]['description'], 'MIGROS ISTANBUL')

    def test_missing_tutar_header_returns_empty(self):
        txs = self.parse(
            [Row('01/07/2026 MIGROS ISTANBUL', '1.234,56')],
            include_tutar_header=False,
        )

        self.assertEqual(txs, [])
