"""One clean, user-scoped list of spend rows for the insight panels.

Rules (see the insights foundation spec):

  * Payments never count: category 'Payment', or the `payment` child tag
    (automatic collections are tagged but not categorised).
  * Cashback never counts as spend. Refunds and cancellations stay and net
    against the category they carry.
  * Non-TRY rows convert through the rates table; a row with no rate for its
    month is dropped and counted in `dropped_no_rate`.
  * basis='billed' keeps every row as the bank posted it.
  * basis='purchased' collapses each installment plan into one row for its
    full amount, dated at the plan's start. One rule covers both installment
    kinds: after a post-purchase conversion the original purchase (+X) and its
    İŞLEM İPTALI (−X) cancel out, and the collapsed plan adds +X.

Amounts stay in TRY here; the lens (real / nominal / USD) is applied by the
caller through RateTable, so one dataset serves every lens.
"""

from collections import defaultdict
from dataclasses import dataclass, field, replace
from datetime import date
from typing import Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

from src.analytics.merchant import normalize
from src.analytics.months import add_months, month_key
from src.analytics.rates import RateTable
from src.models import Expense, ExpenseTag, Tag

BILLED = 'billed'
PURCHASED = 'purchased'
BASES = (BILLED, PURCHASED)

# Child-tag names from the default seed (data/seeds/tags.yaml.example) that
# change how a row counts. A user without these tags gets no special handling.
PAYMENT_TAG = 'payment'
CASHBACK_TAG = 'cashback'
REFUND_TAG = 'refund'
FEE_TAG = 'fee'

CANCEL_MARKERS = ('İŞLEM İPTAL', 'ISLEM IPTAL')
INSTALLMENT_MARKERS = ('TAKSİTLENDİRME/', 'TAKSITLENDIRME/', 'ARTI TAKSIT')


@dataclass(frozen=True)
class SpendRow:
    expense_id: int
    date: date
    month: str                 # YYYY-MM of the transaction date
    amount_try: float
    parent_tag: Optional[str]
    child_tag: Optional[str]
    merchant: str
    card: str
    kind: str                  # purchase | refund | cancel | installment | fee


@dataclass
class SpendSet:
    rows: List[SpendRow] = field(default_factory=list)
    fallbacks: int = 0         # installment-looking rows with no installment columns
    dropped_no_rate: int = 0   # non-TRY rows with no FX rate for their month


def _tags_by_expense(db: Session, user_id: int) -> Dict[int, Tuple[Optional[str], Optional[str]]]:
    """expense id → (parent tag, child tag). Suppressed links don't count.

    Expenses normally carry a parent and one of its children. With several
    children the lowest tag id wins, so the choice is stable between calls.
    """
    names, parents = {}, {}
    for tag in db.query(Tag).filter_by(user_id=user_id):
        names[tag.id] = tag.name
        parents[tag.id] = tag.parent_id

    links = defaultdict(list)
    for expense_id, tag_id in (
        db.query(ExpenseTag.expense_id, ExpenseTag.tag_id)
        .join(Expense, Expense.id == ExpenseTag.expense_id)
        .filter(Expense.user_id == user_id, ExpenseTag.source != 'suppressed')
    ):
        links[expense_id].append(tag_id)

    result = {}
    for expense_id, tag_ids in links.items():
        children = sorted(t for t in tag_ids if parents.get(t) is not None)
        if children:
            child = children[0]
            result[expense_id] = (names.get(parents[child]), names.get(child))
        else:
            result[expense_id] = (names.get(min(tag_ids)), None)
    return result


def _kind(expense: Expense, child_tag: Optional[str]) -> str:
    if expense.installment_count:
        return 'installment'
    if child_tag == REFUND_TAG:
        return 'refund'
    if any(marker in expense.description for marker in CANCEL_MARKERS):
        return 'cancel'
    if child_tag == FEE_TAG:
        return 'fee'
    return 'purchase'


def load_spend(
    db: Session,
    user_id: int,
    *,
    basis: str = BILLED,
    rates: Optional[RateTable] = None,
) -> SpendSet:
    """The user's spend rows, sorted by date. See the module docstring for the rules."""
    if basis not in BASES:
        raise ValueError(f'basis must be one of {BASES}, got {basis!r}')
    rates = rates or RateTable({})
    tags = _tags_by_expense(db, user_id)
    result = SpendSet()
    # plan key → [(installment_no, plan start, total in TRY, row)]
    plans: Dict[tuple, list] = defaultdict(list)

    for expense in db.query(Expense).filter(Expense.user_id == user_id).order_by(Expense.date, Expense.id):
        parent_tag, child_tag = tags.get(expense.id, (None, None))
        if expense.category == 'Payment' or child_tag == PAYMENT_TAG:
            continue
        if child_tag == CASHBACK_TAG:
            continue

        day = expense.date.date()
        month = month_key(day)
        amount = rates.to_try(expense.amount, expense.currency, month)
        if amount is None:
            result.dropped_no_rate += 1
            continue

        merchant = normalize(expense.description)
        row = SpendRow(
            expense_id=expense.id, date=day, month=month, amount_try=amount,
            parent_tag=parent_tag, child_tag=child_tag, merchant=merchant,
            card=expense.card_number or '', kind=_kind(expense, child_tag),
        )
        if not expense.installment_count and any(
                marker in expense.description for marker in INSTALLMENT_MARKERS):
            result.fallbacks += 1

        if (basis == PURCHASED and expense.installment_count
                and expense.installment_total and amount > 0):
            number = expense.installment_no or 1
            start = add_months(day, -(number - 1))
            total = rates.to_try(expense.installment_total, expense.currency, month)
            key = (row.card, merchant, expense.installment_count,
                   round(expense.installment_total, 2), month_key(start))
            plans[key].append((number, start, total, row))
            continue

        result.rows.append(row)

    for members in plans.values():
        _, start, total, first = min(members, key=lambda member: member[0])
        result.rows.append(replace(first, date=start, month=month_key(start),
                                   amount_try=total, kind='purchase'))

    result.rows.sort(key=lambda r: (r.date, r.expense_id))
    return result
