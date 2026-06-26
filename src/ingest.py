"""
Shared transaction ingestion.

Both entry points — the batch importer (`scripts/import_pdfs.py` →
`src.import_pdfs`) and the scheduled Gmail processor (`src.processor`) — funnel
parsed transactions through `ingest_transactions()` so that, regardless of how a
statement enters the system, every expense is:

  * deduplicated against existing rows (date | description | amount | card),
  * stamped with a `statement_period` (from the PDF, or a caller fallback), and
  * returned to the caller so it can be auto-tagged.

Keeping this in one place stops the two paths from drifting apart.  Previously
`make process` inserted expenses with no statement_period, no dedup, and no
tags — leaving them invisible to the dashboard's monthly chart (which filters
on `statement_period IS NOT NULL`) and uncategorised.
"""

import logging
from datetime import datetime
from typing import Iterable, List, Optional, Set, Tuple

from sqlalchemy.orm import Session

from src.models import Expense

logger = logging.getLogger(__name__)


def fingerprint(date, description: str, amount: float, card_number: str) -> str:
    """Stable key used to deduplicate a transaction."""
    date_str = date.strftime('%Y-%m-%d') if isinstance(date, datetime) else str(date)
    return f"{date_str}|{description}|{amount:.2f}|{card_number}"


def load_fingerprints(db: Session) -> Set[str]:
    """Load fingerprints for every existing expense (for dedup)."""
    fingerprints: Set[str] = set()
    for exp in db.query(
        Expense.date, Expense.description, Expense.amount, Expense.card_number
    ).all():
        fingerprints.add(fingerprint(exp.date, exp.description, exp.amount, exp.card_number))
    return fingerprints


def ingest_transactions(
    db: Session,
    transactions: Iterable[dict],
    *,
    bank_source: str,
    fallback_period: Optional[str] = None,
    fingerprints: Optional[Set[str]] = None,
    dry_run: bool = False,
) -> Tuple[List[Expense], dict]:
    """Insert parsed transactions, skipping duplicates and non-monetary lines.

    Args:
        db: SQLAlchemy session. The caller is responsible for ``commit()``.
        transactions: parsed transaction dicts from a bank parser.
        bank_source: bank id stored on each expense.
        fallback_period: ``YYYY-MM`` used when a transaction carries no
            ``statement_period`` of its own.
        fingerprints: mutable dedup set. Loaded from the DB when not supplied.
            Pass the same set across calls to dedup within a batch.
        dry_run: build/count expenses without adding them to the session.

    Returns:
        ``(new_expenses, stats)`` where ``stats`` has the keys ``inserted``,
        ``duplicates`` and ``skipped_no_amount``.
    """
    if fingerprints is None:
        fingerprints = load_fingerprints(db)

    new_expenses: List[Expense] = []
    stats = {'inserted': 0, 'duplicates': 0, 'skipped_no_amount': 0}

    for tx in transactions:
        # Non-monetary lines (e.g. MaxiPuan point operations) carry no amount.
        if tx.get('amount') is None:
            stats['skipped_no_amount'] += 1
            continue

        fp = fingerprint(
            tx['date'], tx['description'], tx['amount'], tx.get('card_number', '')
        )
        if fp in fingerprints:
            stats['duplicates'] += 1
            continue

        # Prefer the period parsed from the PDF (Hesap Kesim Tarihi); fall back
        # to a caller-supplied period (e.g. derived from the filename date).
        period = tx.get('statement_period') or fallback_period

        expense = Expense(
            date=tx['date'],
            description=tx['description'].strip(),
            amount=tx['amount'],
            currency=tx.get('currency', 'TRY'),
            category=tx.get('category'),
            bank_source=bank_source,
            card_number=tx.get('card_number', ''),
            statement_period=period,
            raw_text=tx.get('raw_text'),
        )
        if not dry_run:
            db.add(expense)

        new_expenses.append(expense)
        fingerprints.add(fp)
        stats['inserted'] += 1

    return new_expenses, stats
