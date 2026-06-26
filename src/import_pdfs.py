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
from sqlalchemy.orm import Session
from src.models import Expense, ProcessedEmail
from src.parsers.isbank import IsbankParser
from src.tag_engine import TagEngine
from src.ingest import ingest_transactions, load_fingerprints

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

    # Load message IDs imported in PRIOR runs (SUCCESS only). Used solely to
    # skip re-parsing PDFs we've already imported — it is NOT updated mid-loop,
    # so sibling PDFs from the same email (different cards) are still processed.
    already_processed = set()
    if not clear:
        for row in db.query(ProcessedEmail.message_id).filter_by(status='SUCCESS').all():
            already_processed.add(row.message_id)
        if already_processed:
            logger.info(f"Found {len(already_processed)} already-processed message IDs")

    # Build a set of existing expense fingerprints for dedup
    existing_fingerprints = set() if clear else load_fingerprints(db)
    if existing_fingerprints:
        logger.info(f"Loaded {len(existing_fingerprints)} existing expense fingerprints")

    # Parse each PDF
    parser = IsbankParser()
    new_expenses = []
    # Per-email outcome for THIS run. Written to processed_emails once, after the
    # loop, so a single email's multiple card PDFs don't fight over its status
    # and SUCCESS always wins over FAILED.
    email_outcomes: dict = {}

    for pdf_path in pdf_files:
        basename = os.path.basename(pdf_path)
        fields = parse_pdf_filename(basename)
        email_id = fields.get('emailid')

        # Skip only if a PRIOR run already imported this email's statements.
        # The fingerprint dedup below is the real correctness guarantee; this is
        # just an optimisation to avoid re-parsing PDFs.
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

            new, ing = ingest_transactions(
                db,
                transactions,
                bank_source=fields.get('bank', 'isbank'),
                fallback_period=filename_period,
                fingerprints=existing_fingerprints,
                dry_run=dry_run,
            )
            new_expenses.extend(new)
            stats['new_inserted'] += ing['inserted']
            stats['duplicates_skipped'] += ing['duplicates']

            if email_id:
                email_outcomes[email_id] = 'SUCCESS'

        except Exception as e:
            logger.error(f"Error parsing {basename}: {e}")
            stats['errors'] += 1

            # Record the failure so it's visible; it is retried next run. A
            # SUCCESS from a sibling PDF of the same email takes precedence.
            if email_id and email_outcomes.get(email_id) != 'SUCCESS':
                email_outcomes[email_id] = 'FAILED'

    if not dry_run:
        # Upsert one processed_emails row per email (avoids duplicate-key errors
        # when retrying a previously-FAILED email, and dedups multi-card emails).
        for eid, status in email_outcomes.items():
            record = db.query(ProcessedEmail).filter_by(message_id=eid).first()
            if record:
                record.status = status
            else:
                db.add(ProcessedEmail(message_id=eid, status=status))

        db.commit()
        if new_expenses:
            logger.info(f"Inserted {stats['new_inserted']} new expenses")

            # Auto-tag new expenses
            engine = TagEngine(db)
            stats['tags_applied'] = engine.apply_rules(new_expenses)
            logger.info(f"Applied {stats['tags_applied']} tags to new expenses")

    return stats
