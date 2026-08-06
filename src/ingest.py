"""
Shared transaction ingestion.

Both entry points — the batch importer (`scripts/import_pdfs.py` →
`src.import_pdfs`) and the scheduled Gmail processor (`src.processor`) — funnel
parsed transactions through `ingest_transactions()` so that, regardless of how a
statement enters the system, every expense is:

  * deduplicated against existing rows (date | description | amount | card |
    statement_period | occurrence-within-statement),
  * stamped with a `statement_period` (from the PDF, or a caller fallback), and
  * returned to the caller so it can be auto-tagged.

Keeping this in one place stops the two paths from drifting apart.  Previously
`make process` inserted expenses with no statement_period, no dedup, and no
tags — leaving them invisible to the dashboard's monthly chart (which filters
on `statement_period IS NOT NULL`) and uncategorised.
"""

import logging
from collections import Counter
from datetime import datetime
from typing import Iterable, List, Optional, Set, Tuple

from sqlalchemy.orm import Session

from src.models import Expense

logger = logging.getLogger(__name__)


def fingerprint(
    date,
    description: str,
    amount: float,
    card_number: str,
    statement_period: Optional[str] = None,
    occurrence: int = 1,
) -> str:
    """Stable key used to deduplicate a transaction.

    ``occurrence`` distinguishes repeated identical rows *within one statement*.
    A statement can legitimately print the same date, description, amount and
    card twice — two separate purchases of the same thing on the same day, e.g.
    a fixed-price transit fare. Without an occurrence index those collapse into
    one row: a live run over 133 real İşbank statements lost 52 transactions
    exactly this way.

    ``statement_period`` keeps occurrence numbering from colliding across
    periods. On the same real data no fingerprint ever spanned two periods, so
    including it changes nothing observed — it is here so that a future
    cross-period collision cannot silently renumber occurrences.
    """
    date_str = date.strftime('%Y-%m-%d') if isinstance(date, datetime) else str(date)
    return (
        f"{date_str}|{description}|{amount:.2f}|{card_number}"
        f"|{statement_period or ''}|{occurrence}"
    )


def _dedup_group(date, description: str, amount: float, card_number: str,
                 statement_period: Optional[str]) -> tuple:
    """The part of a fingerprint that repeated rows share."""
    date_str = date.strftime('%Y-%m-%d') if isinstance(date, datetime) else str(date)
    return (date_str, description, round(amount, 2), card_number, statement_period or '')


def load_fingerprints(db: Session, user_id: int) -> Set[str]:
    """Load fingerprints for a user's existing expenses (for dedup).

    Rows sharing a group get occurrence numbers 1..n, so re-importing a
    statement that legitimately repeats a row still dedups: its second parsed
    occurrence matches the stored second occurrence.
    """
    counts: Counter = Counter()
    fingerprints: Set[str] = set()
    for exp in db.query(
        Expense.date, Expense.description, Expense.amount, Expense.card_number,
        Expense.statement_period,
    ).filter(Expense.user_id == user_id).all():
        group = _dedup_group(exp.date, exp.description, exp.amount,
                             exp.card_number, exp.statement_period)
        counts[group] += 1
        fingerprints.add(fingerprint(
            exp.date, exp.description, exp.amount, exp.card_number,
            exp.statement_period, counts[group],
        ))
    return fingerprints


def ingest_transactions(
    db: Session,
    transactions: Iterable[dict],
    *,
    user_id: int,
    bank_source: str,
    fallback_period: Optional[str] = None,
    fingerprints: Optional[Set[str]] = None,
    dry_run: bool = False,
) -> Tuple[List[Expense], dict]:
    """Insert parsed transactions, skipping duplicates and non-monetary lines.

    Args:
        db: SQLAlchemy session. The caller is responsible for ``commit()``.
        transactions: parsed transaction dicts from a bank parser.
        user_id: the owning user; set on every inserted expense and used to
            scope deduplication.
        bank_source: bank id stored on each expense.
        fallback_period: ``YYYY-MM`` used when a transaction carries no
            ``statement_period`` of its own.
        fingerprints: mutable dedup set. Loaded from the DB (for this user) when
            not supplied. Pass the same set across calls to dedup within a batch.
        dry_run: build/count expenses without adding them to the session.

    Returns:
        ``(new_expenses, stats)`` where ``stats`` has the keys ``inserted``,
        ``duplicates`` and ``skipped_no_amount``.
    """
    if fingerprints is None:
        fingerprints = load_fingerprints(db, user_id)

    new_expenses: List[Expense] = []
    stats = {'inserted': 0, 'duplicates': 0, 'skipped_no_amount': 0}

    # Occurrence numbering is scoped to this call, i.e. to one statement, since
    # both callers invoke this once per PDF. That is what lets a statement keep
    # its own repeated rows while a re-sent statement still dedups: the re-send
    # numbers its rows 1..n identically and every fingerprint collides.
    occurrences: Counter = Counter()

    for tx in transactions:
        # Non-monetary lines (e.g. MaxiPuan point operations) carry no amount.
        if tx.get('amount') is None:
            stats['skipped_no_amount'] += 1
            continue

        # Prefer the period parsed from the PDF (Hesap Kesim Tarihi); fall back
        # to a caller-supplied period (e.g. derived from the filename date).
        period = tx.get('statement_period') or fallback_period

        group = _dedup_group(
            tx['date'], tx['description'], tx['amount'],
            tx.get('card_number', ''), period,
        )
        occurrences[group] += 1
        fp = fingerprint(
            tx['date'], tx['description'], tx['amount'],
            tx.get('card_number', ''), period, occurrences[group],
        )
        if fp in fingerprints:
            stats['duplicates'] += 1
            continue

        expense = Expense(
            user_id=user_id,
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
