"""
Bulk import PDFs from data/pdfs/ into the expense database.

Scans the data/pdfs/ directory for statement PDFs, parses each one,
deduplicates against existing records, and auto-tags new expenses.

Supports two filename formats:
  New: bank(isbank),date(2019-06-07),emailid(18abc),card(6152).pdf
  Old: isbank_2019-06-07_124_originalname.pdf  (backward compat)

File-level tracking: the Gmail message_id is extracted from the filename
and checked against the processed_emails table — the same table used by
`make process`. Both flows share a single source of truth.
"""

import glob
import logging
import os
import re
from datetime import datetime
from sqlalchemy.orm import Session
from src.models import Expense, ProcessedEmail
from src.parsers.isbank import IsbankParser
from src.tag_engine import TagEngine

logger = logging.getLogger(__name__)

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data', 'pdfs')

# Regex for new structured filename: key(value) pairs
_FIELD_RE = re.compile(r'(\w+)\(([^)]*)\)')


def parse_pdf_filename(basename: str) -> dict:
    """Parse a PDF filename into a dict of metadata fields.

    Supports two formats:
      New: bank(isbank),date(2019-06-07),emailid(abc123),card(6152).pdf
           → {'bank': 'isbank', 'date': '2019-06-07', 'emailid': 'abc123', 'card': '6152'}

      Old: isbank_2019-06-07_124_original.pdf  (backward compat)
           → {'bank': 'isbank', 'date': '2019-06-07'}
           (no emailid — old format used sequential indices, not Gmail IDs)
    """
    stem = basename.rsplit('.', 1)[0] if '.' in basename else basename

    # Try new format first
    fields = {}
    for m in _FIELD_RE.finditer(stem):
        fields[m.group(1)] = m.group(2)

    if fields:
        return fields

    # Fall back to old underscore format: bank_date_index_original
    parts = stem.split('_')
    if len(parts) >= 2:
        result = {'bank': parts[0], 'date': parts[1]}
        # Old format used sequential indices (e.g. "124"), not Gmail IDs.
        # Don't treat them as emailid — they won't match processed_emails.
        return result

    return {}


def import_pdfs(db: Session, clear: bool = False, dry_run: bool = False) -> dict:
    """
    Import all statement PDFs from data/pdfs/ into the database.

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
        'files_skipped': 0,
        'transactions_parsed': 0,
        'new_inserted': 0,
        'duplicates_skipped': 0,
        'errors': 0,
        'tags_applied': 0,
    }

    # Find all PDF files (both old and new naming formats)
    pdf_files = sorted(
        glob.glob(os.path.join(DATA_DIR, 'bank(*.pdf'))
        + glob.glob(os.path.join(DATA_DIR, 'isbank_*.pdf'))
    )
    stats['files_found'] = len(pdf_files)

    if not pdf_files:
        logger.warning(f"No PDF files found in {DATA_DIR}")
        return stats

    logger.info(f"Found {len(pdf_files)} PDF files in {DATA_DIR}")

    # Clear if requested
    if clear and not dry_run:
        from src.models import ExpenseTag
        db.query(ExpenseTag).delete()
        deleted = db.query(Expense).delete()
        db.query(ProcessedEmail).delete()
        db.commit()
        logger.info(f"Cleared {deleted} existing expenses, their tags, and processed email records")

    # Load already-processed message IDs
    already_processed = set()
    if not clear:
        for row in db.query(ProcessedEmail.message_id).filter_by(status='SUCCESS').all():
            already_processed.add(row.message_id)
        if already_processed:
            logger.info(f"Found {len(already_processed)} already-processed message IDs")

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
        fields = parse_pdf_filename(basename)
        email_id = fields.get('emailid')

        # Skip if already processed successfully
        if email_id and email_id in already_processed:
            logger.debug(f"Skipping {basename} — message {email_id} already processed")
            stats['files_skipped'] += 1
            continue

        # Statement period from filename date (YYYY-MM)
        filename_period = None
        date_str = fields.get('date', '')
        if len(date_str) >= 7:
            filename_period = date_str[:7]  # "2026-04"

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
                        bank_source=fields.get('bank', 'isbank'),
                        card_number=tx.get('card_number', ''),
                        statement_period=period,
                        raw_text=tx.get('raw_text'),
                    )
                    db.add(expense)
                    new_expenses.append(expense)

                existing_fingerprints.add(fp)
                stats['new_inserted'] += 1

            # Mark this email as processed
            if not dry_run and email_id:
                record = ProcessedEmail(message_id=email_id, status='SUCCESS')
                db.add(record)
                already_processed.add(email_id)

        except Exception as e:
            logger.error(f"Error parsing {basename}: {e}")
            stats['errors'] += 1

            # Record the failure so we can see it, but it will be retried next run
            if not dry_run and email_id and email_id not in already_processed:
                record = ProcessedEmail(message_id=email_id, status='FAILED')
                db.add(record)

    if not dry_run:
        db.commit()
        if new_expenses:
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
