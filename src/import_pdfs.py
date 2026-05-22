"""
Bulk import PDFs from data/pdfs/ into the expense database.

Scans the data/pdfs/ directory for İşbank statement PDFs, parses each one,
deduplicates against existing records, and auto-tags new expenses.
"""

import glob
import logging
import os
from datetime import datetime
from sqlalchemy.orm import Session
from src.models import Expense
from src.parsers.isbank import IsbankParser
from src.tag_engine import TagEngine

logger = logging.getLogger(__name__)

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data', 'pdfs')


def import_pdfs(db: Session, clear: bool = False, dry_run: bool = False) -> dict:
    """
    Import all İşbank PDFs from data/pdfs/ into the database.

    Args:
        db: SQLAlchemy session
        clear: If True, delete all existing expenses before importing
        dry_run: If True, parse and count but don't insert

    Returns:
        Dict with import statistics
    """
    stats = {
        'files_found': 0,
        'files_parsed': 0,
        'transactions_parsed': 0,
        'new_inserted': 0,
        'duplicates_skipped': 0,
        'errors': 0,
        'tags_applied': 0,
    }

    # Find all PDF files
    pdf_pattern = os.path.join(DATA_DIR, 'isbank_*.pdf')
    pdf_files = sorted(glob.glob(pdf_pattern))
    stats['files_found'] = len(pdf_files)

    if not pdf_files:
        logger.warning(f"No PDF files found matching {pdf_pattern}")
        return stats

    logger.info(f"Found {len(pdf_files)} PDF files in {DATA_DIR}")

    # Clear if requested
    if clear and not dry_run:
        from src.models import ExpenseTag
        db.query(ExpenseTag).delete()
        deleted = db.query(Expense).delete()
        db.commit()
        logger.info(f"Cleared {deleted} existing expenses and their tags")

    # Build a set of existing expense fingerprints for dedup
    existing_fingerprints = set()
    if not clear:
        for exp in db.query(Expense.date, Expense.description, Expense.amount, Expense.card_number).all():
            fp = _fingerprint(exp.date, exp.description, exp.amount, exp.card_number)
            existing_fingerprints.add(fp)
        logger.info(f"Loaded {len(existing_fingerprints)} existing expense fingerprints")

    # Parse each PDF
    parser = IsbankParser()
    new_expenses = []

    for pdf_path in pdf_files:
        basename = os.path.basename(pdf_path)

        # Fallback statement period from filename (YYYY-MM)
        # Filename format: isbank_YYYY-MM-DD_NNN_...
        filename_period = None
        parts = basename.split('_')
        if len(parts) >= 2:
            date_part = parts[1]  # e.g. "2026-04-30"
            if len(date_part) >= 7:
                filename_period = date_part[:7]  # "2026-04"

        try:
            transactions = parser.extract_transactions(pdf_path)
            stats['files_parsed'] += 1
            stats['transactions_parsed'] += len(transactions)

            for tx in transactions:
                # Skip entries with no TUTAR amount (non-monetary lines
                # like MaxiPuan point operations)
                if tx['amount'] is None:
                    continue

                fp = _fingerprint(tx['date'], tx['description'], tx['amount'], tx.get('card_number', ''))

                if fp in existing_fingerprints:
                    stats['duplicates_skipped'] += 1
                    continue

                # Prefer statement_period from Hesap Kesim Tarihi in the PDF,
                # fall back to filename-derived period
                period = tx.get('statement_period') or filename_period

                if not dry_run:
                    expense = Expense(
                        date=tx['date'],
                        description=tx['description'].strip(),
                        amount=tx['amount'],
                        currency=tx.get('currency', 'TRY'),
                        category=tx.get('category'),
                        bank_source='isbank',
                        card_number=tx.get('card_number', ''),
                        statement_period=period,
                        raw_text=tx.get('raw_text'),
                    )
                    db.add(expense)
                    new_expenses.append(expense)

                existing_fingerprints.add(fp)
                stats['new_inserted'] += 1

        except Exception as e:
            logger.error(f"Error parsing {basename}: {e}")
            stats['errors'] += 1

    if not dry_run and new_expenses:
        db.commit()
        logger.info(f"Inserted {stats['new_inserted']} new expenses")

        # Auto-tag new expenses
        engine = TagEngine(db)
        stats['tags_applied'] = engine.apply_rules(new_expenses)
        logger.info(f"Applied {stats['tags_applied']} tags to new expenses")

    return stats


def _fingerprint(date: datetime, description: str, amount: float, card_number: str) -> str:
    """Create a unique fingerprint for deduplication."""
    date_str = date.strftime('%Y-%m-%d') if isinstance(date, datetime) else str(date)
    return f"{date_str}|{description}|{amount:.2f}|{card_number}"

