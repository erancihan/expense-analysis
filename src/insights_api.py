"""Read-only JSON endpoints behind the /insights page.

/meta tells the page what it may offer (which lenses have rates, which months
are fully covered by statements, the tag colours); /trend serves one lens,
basis and month range at a time. Both are GET, scoped to the current user and
computed on request: nothing derived from a user's data is stored.

The fail-closed auth gate in src.web covers these routes because they are not
in its public allowlist.
"""

import logging
import re
from typing import List, Optional

from flask import Blueprint, jsonify, request
from flask_login import current_user
from sqlalchemy.orm import Session

from src.analytics.coverage import coverage
from src.analytics.dataset import BASES, BILLED, load_spend
from src.analytics.rates import CPI_TR, USDTRY, RateTable
from src.analytics.trend import (
    CPI_HINT, LENSES, NOMINAL, REAL, USD, build_trend, lens_available,
)
from src.extensions import with_db
from src.models import Tag

logger = logging.getLogger(__name__)

insights_bp = Blueprint('insights', __name__, url_prefix='/api/insights')

MONTH_RE = re.compile(r'^\d{4}-(0[1-9]|1[0-2])$')

_LABELS = {REAL: "Today's ₺", NOMINAL: 'Nominal ₺', USD: 'USD'}
_REASONS = {REAL: CPI_HINT, USD: 'No FX rates stored: run make fetch-rates.'}


def _lens_options(rates: RateTable) -> List[dict]:
    options = []
    for lens in LENSES:
        available = lens_available(lens, rates)
        options.append({'id': lens, 'label': _LABELS[lens], 'available': available,
                        'reason': None if available else _REASONS.get(lens)})
    return options


def _default_lens(rates: RateTable) -> str:
    return REAL if lens_available(REAL, rates) else NOMINAL


def _bad(message: str):
    return jsonify({'error': message}), 400


@insights_bp.route('/meta')
@with_db
def api_insights_meta(db: Session):
    rates = RateTable.load(db)
    cov = coverage(db, current_user.id)
    cpi_latest = rates.latest(CPI_TR)
    return jsonify({
        'lenses': _lens_options(rates),
        'default_lens': _default_lens(rates),
        'bases': list(BASES),
        'rates': {
            'cpi_latest': cpi_latest,
            'cpi_provisional': bool(cpi_latest and rates.is_provisional(CPI_TR, cpi_latest)),
            'fx_latest': rates.latest(USDTRY),
        },
        'coverage': cov,
        'first_month': min(cov) if cov else None,
        'last_month': max(cov) if cov else None,
        'tag_colors': {name: color for name, color in
                       db.query(Tag.name, Tag.color).filter_by(user_id=current_user.id)},
    })


@insights_bp.route('/trend')
@with_db
def api_insights_trend(db: Session):
    rates = RateTable.load(db)
    lens = request.args.get('lens') or _default_lens(rates)
    basis = request.args.get('basis') or BILLED
    from_arg: Optional[str] = request.args.get('from') or None
    to_arg: Optional[str] = request.args.get('to') or None
    parent = request.args.get('parent') or None

    if lens not in LENSES:
        return _bad(f'lens must be one of {", ".join(LENSES)}')
    if not lens_available(lens, rates):
        return _bad(_REASONS[lens])
    if basis not in BASES:
        return _bad(f'basis must be one of {", ".join(BASES)}')
    for name, value in (('from', from_arg), ('to', to_arg)):
        if value is not None and not MONTH_RE.match(value):
            return _bad(f'{name} must be a month as YYYY-MM')
    if from_arg and to_arg and from_arg > to_arg:
        return _bad('from must not be after to')

    cov = coverage(db, current_user.id)
    if not cov:
        payload = build_trend([], {}, lens, rates, '', '', parent)
        return jsonify({'lens': lens, 'basis': basis, 'from': None, 'to': None, **payload})

    first, last = min(cov), max(cov)
    start = max(from_arg or first, first)
    end = min(to_arg or last, last)
    if start > end:
        # The requested window lies wholly outside the data.
        start, end = first, last
    rows = load_spend(db, current_user.id, basis=basis, rates=rates).rows
    payload = build_trend(rows, cov, lens, rates, start, end, parent)
    return jsonify({'lens': lens, 'basis': basis, 'from': start, 'to': end, **payload})
