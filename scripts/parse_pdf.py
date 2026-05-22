#!/usr/bin/env python3
"""
Parse İşbank PDF statements and display transactions.

Usage:
    ./scripts/parse_pdf.py <pdf_path> [pdf_path ...]
    ./scripts/parse_pdf.py --dump-html <pdf_path>     # Dump intermediate HTML
"""

import logging
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.parsers.isbank import IsbankParser

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)-35s - %(levelname)-7s - %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)


def main():
    if len(sys.argv) < 2:
        print("Usage: ./scripts/parse_pdf.py <pdf_path> [pdf_path ...]")
        print()
        print("Options:")
        print("  --dump-html   Dump the intermediate HTML to stdout instead of parsing")
        sys.exit(1)

    dump_html = '--dump-html' in sys.argv
    paths = [a for a in sys.argv[1:] if not a.startswith('--')]

    parser = IsbankParser()

    for pdf_path in paths:
        print(f"\n{'='*80}")
        print(f"FILE: {pdf_path}")
        print(f"{'='*80}")

        if dump_html:
            html = parser._pdf_to_html(pdf_path)
            print(html)
            continue

        transactions = parser.extract_transactions(pdf_path)

        if not transactions:
            print("  (no transactions found)")
            continue

        # Print card identity
        first = transactions[0]
        card_label = first.get('card_type', '') or 'Unknown'
        card_num = first.get('card_number', '') or '????'
        print(f"  Card: {card_label} ({card_num})")
        print()

        total = 0.0
        for i, tx in enumerate(transactions):
            date_str = tx['date'].strftime('%d/%m/%Y')
            sign = '+' if tx['amount'] >= 0 else ''
            cur = tx['currency']
            amt_str = f"{sign}{tx['amount']:,.2f} {cur}"
            cat = f"  [{tx['category']}]" if tx['category'] != 'Uncategorized' else ''
            print(f"  {i+1:3d}. {date_str}  {amt_str:>18s}  {tx['description'][:80]}{cat}")
            total += tx['amount']

        print(f"\n  {'TOTAL':>36s}: {total:,.2f}")
        print(f"  {'Transaction count':>36s}: {len(transactions)}")


if __name__ == '__main__':
    main()
