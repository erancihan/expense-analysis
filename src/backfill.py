"""One-off backfill: link existing expenses to statements, in place.

The parser fix that reads the closing date from the label's own row moves some
statements back by a month, and the parser now also reads the installment
column. Existing rows need both. Re-importing is not an option:

  * src/ingest.py puts statement_period in the dedup fingerprint, so importing
    on top of the old rows would insert every moved row a second time, and
  * import_pdfs(clear=True) deletes every tag link. A tag the user confirmed in
    the UI can be stored as source='auto' (POST /api/expenses/<id>/tags/<tag>
    does not promote an existing auto row), so re-running the tag engine
    afterwards would not restore the user's work.

So this re-parses the PDFs and updates the matching rows where they are. It
never inserts or deletes an expense and never runs the tag engine, and it rolls
back if any expense's tag set is different at the end.

It also corrects `currency` where the parser now reads it differently: a USD
statement whose balance carried a sign ("+11,58 USD") used to be read as TRY.
Currency is not part of the dedup fingerprint, so this cannot create duplicates.
"""

import logging
import os
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Callable, Dict, Iterable, List, Tuple

from sqlalchemy.orm import Session

from src.import_pdfs import parse_pdf_filename
from src.ingest import get_or_create_statement
from src.models import Expense, ExpenseTag

logger = logging.getLogger(__name__)

TagSnapshot = Dict[int, Tuple[Tuple[int, str], ...]]


class TagSetChanged(RuntimeError):
    """An expense's tag set differed at the end of the backfill; nothing was written."""


@dataclass
class BackfillReport:
    statements: int = 0
    matched: int = 0
    installments: int = 0
    period_changes: Counter = field(default_factory=Counter)        # (old, new) → rows
    currency_changes: List[Tuple[int, str, str]] = field(default_factory=list)  # (id, old, new)
    duplicate_files: List[Tuple[str, str]] = field(default_factory=list)   # (skipped, kept)
    files_without_closing_date: List[str] = field(default_factory=list)
    parsed_unmatched: List[Tuple[str, str, str, float]] = field(default_factory=list)
    db_unmatched: List[int] = field(default_factory=list)            # expense ids


def tag_snapshot(db: Session, user_id: int) -> TagSnapshot:
    """Every expense's full tag set: auto, manual and suppressed rows alike."""
    rows = (
        db.query(ExpenseTag.expense_id, ExpenseTag.tag_id, ExpenseTag.source)
        .join(Expense, Expense.id == ExpenseTag.expense_id)
        .filter(Expense.user_id == user_id)
        .all()
    )
    grouped = defaultdict(list)
    for row in rows:
        grouped[row.expense_id].append((row.tag_id, row.source))
    return {expense_id: tuple(sorted(tags)) for expense_id, tags in grouped.items()}


def _match_key(date, description: str, amount: float, card_number: str) -> tuple:
    """The dedup fingerprint minus the period, which is what this fixes."""
    return (date.strftime('%Y-%m-%d'), description.strip(), round(amount, 2), card_number or '')


def backfill_statements(
    db: Session,
    user_id: int,
    pdf_paths: Iterable[str],
    extract: Callable[[str], List[dict]],
    *,
    apply: bool = False,
) -> BackfillReport:
    """Link the user's expenses to statements and fill their installment fields.

    Args:
        db: session; committed when ``apply`` is true, rolled back otherwise.
        user_id: whose expenses to update.
        pdf_paths: statement PDFs, in the order to process them. The first PDF
            for a (card, closing date, currency) wins; later ones are
            re-deliveries.
        extract: parser function, path → transaction dicts
            (``IsbankParser().extract_transactions``).
        apply: write the changes. False is a dry run with the same report.

    Raises:
        TagSetChanged: if any expense's tag set changed; the session is rolled back.
    """
    report = BackfillReport()
    before = tag_snapshot(db, user_id)

    # Rows that share a key are matched in id order, which mirrors the
    # occurrence numbering ingest.py uses within one statement.
    pool: Dict[tuple, List[Expense]] = defaultdict(list)
    for expense in db.query(Expense).filter_by(user_id=user_id).order_by(Expense.id):
        pool[_match_key(expense.date, expense.description, expense.amount,
                        expense.card_number)].append(expense)
    used = set()
    kept_file: Dict[tuple, str] = {}

    try:
        for path in pdf_paths:
            name = os.path.basename(path)
            transactions = [tx for tx in extract(path) if tx.get('amount') is not None]
            if not transactions:
                continue
            closing_date = transactions[0].get('closing_date')
            card_number = transactions[0].get('card_number', '')
            currency = transactions[0].get('currency', 'TRY')
            if closing_date is None:
                report.files_without_closing_date.append(name)
                continue
            identity = (card_number, closing_date, currency)
            if identity in kept_file:
                report.duplicate_files.append((name, kept_file[identity]))
                continue
            kept_file[identity] = name

            statement = get_or_create_statement(
                db,
                user_id=user_id,
                card_number=card_number,
                closing_date=closing_date,
                currency=currency,
                source_file=name,
                email_id=parse_pdf_filename(name).get('emailid'),
            )
            report.statements += 1
            period = closing_date.strftime('%Y-%m')

            for tx in transactions:
                key = _match_key(tx['date'], tx['description'], tx['amount'],
                                 tx.get('card_number', ''))
                expense = next((e for e in pool.get(key, []) if e.id not in used), None)
                if expense is None:
                    report.parsed_unmatched.append((name, key[0], key[1], key[2]))
                    continue
                used.add(expense.id)
                report.matched += 1
                if expense.statement_period != period:
                    report.period_changes[(expense.statement_period, period)] += 1
                if (expense.currency or 'TRY') != currency:
                    report.currency_changes.append((expense.id, expense.currency, currency))
                    expense.currency = currency
                expense.statement_id = statement.id
                expense.statement_period = period
                expense.installment_no = tx.get('installment_no')
                expense.installment_count = tx.get('installment_count')
                expense.installment_total = tx.get('installment_total')
                if expense.installment_count:
                    report.installments += 1

        report.db_unmatched = sorted(
            e.id for rows in pool.values() for e in rows if e.id not in used)
        db.flush()
        if tag_snapshot(db, user_id) != before:
            raise TagSetChanged(
                'An expense tag set changed during the backfill; everything was rolled back.')
    except BaseException:
        db.rollback()
        raise

    if apply:
        db.commit()
    else:
        db.rollback()
    return report
