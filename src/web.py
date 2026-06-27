"""
Flask web server for the Expense Analysis application.

Serves a single-page HTML frontend and a JSON REST API for
expenses, tags, and tag rules.
"""

import logging
import os
from datetime import datetime, timedelta
from functools import wraps

from flask import Flask, jsonify, request, send_from_directory, render_template, redirect, url_for, Response
from flask_login import LoginManager, current_user
from werkzeug.middleware.proxy_fix import ProxyFix
from sqlalchemy import func, extract
from sqlalchemy.orm import Session

from src.config import SECRET_KEY, SESSION_COOKIE_SECURE, TRUSTED_PROXIES
from src.database import SessionLocal
from src.models import Expense, Tag, TagRule, ExpenseTag, User
from src.tag_engine import TagEngine
from src.auth import auth_bp
from src.extensions import csrf, limiter

logger = logging.getLogger(__name__)

# ── App setup ────────────────────────────────────────────────────────────────

STATIC_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'static')
TEMPLATE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'templates')

app = Flask(__name__, static_folder=STATIC_DIR, template_folder=TEMPLATE_DIR)
app.json.ensure_ascii = False

# Behind a reverse proxy, read the real client IP / scheme from X-Forwarded-*
# (needed for correct rate-limiting and Secure-cookie/HTTPS detection).
if TRUSTED_PROXIES > 0:
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=TRUSTED_PROXIES, x_proto=TRUSTED_PROXIES)

# ── Auth / sessions ──────────────────────────────────────────────────────────

app.secret_key = SECRET_KEY
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE='Lax',
    SESSION_COOKIE_SECURE=SESSION_COOKIE_SECURE,
)

# CSRF protection (state-changing requests) and login rate limiting.
csrf.init_app(app)
limiter.init_app(app)

login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'auth.login'


@login_manager.user_loader
def load_user(user_id: str):
    db = SessionLocal()
    try:
        return db.get(User, int(user_id))
    finally:
        db.close()


@login_manager.unauthorized_handler
def unauthorized():
    # JSON for the API, a redirect-to-login for page requests.
    if request.path.startswith('/api/'):
        return jsonify({'error': 'Authentication required'}), 401
    return redirect(url_for('auth.login', next=request.path))


app.register_blueprint(auth_bp)

# Allowlisted endpoints reachable without a session. Everything else is denied
# by default (fail-closed), so new routes are protected unless added here.
_PUBLIC_ENDPOINTS = {'auth.login', 'static'}


@app.before_request
def require_login():
    if request.endpoint in _PUBLIC_ENDPOINTS:
        return None
    if not current_user.is_authenticated:
        return login_manager.unauthorized()
    return None


# ── Security headers ────────────────────────────────────────────────────────

# Content-Security-Policy. 'unsafe-inline'/'unsafe-eval' are required by the
# current stack (the Tailwind Play CDN and Alpine.js evaluate expressions at
# runtime); the policy still restricts *sources* to self + the known CDNs and
# locks down framing, base-uri and form-action. Tightening to a nonce-based
# policy requires vendoring Tailwind + Alpine's CSP build (tracked in ROADMAP).
_CSP = "; ".join([
    "default-src 'self'",
    "script-src 'self' 'unsafe-inline' 'unsafe-eval' https://cdn.tailwindcss.com https://cdn.jsdelivr.net",
    "style-src 'self' 'unsafe-inline' https://cdn.tailwindcss.com https://fonts.googleapis.com",
    "font-src 'self' https://fonts.gstatic.com",
    "img-src 'self' data:",
    "connect-src 'self'",
    "frame-ancestors 'none'",
    "base-uri 'self'",
    "form-action 'self'",
])


@app.after_request
def add_security_headers(response: Response) -> Response:
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'DENY'
    response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
    response.headers.setdefault('Content-Security-Policy', _CSP)
    return response


# ── DB session helper ────────────────────────────────────────────────────────

def get_session() -> Session:
    return SessionLocal()


def with_db(f):
    """Decorator to inject a DB session and handle cleanup."""
    @wraps(f)
    def wrapper(*args, **kwargs):
        db = get_session()
        try:
            result = f(db, *args, **kwargs)
            return result
        except Exception as e:
            db.rollback()
            logger.error(f"API error in {f.__name__}: {e}", exc_info=True)
            return jsonify({'error': 'Internal server error'}), 500
        finally:
            db.close()
    return wrapper


def _owns_expense(db: Session, expense_id: int) -> bool:
    """True if the expense exists and belongs to the current user."""
    return db.query(Expense.id).filter_by(
        id=expense_id, user_id=current_user.id
    ).first() is not None


# ── Static files ─────────────────────────────────────────────────────────────

@app.route('/')
def index():
    return render_template('index.html')

def parse_date_filter(date_str: str, is_end: bool = False):
    if not date_str: return None
    try:
        dt = datetime.strptime(date_str, '%Y-%m-%d')
        if is_end:
            return dt + timedelta(days=1)
        return dt
    except ValueError:
        pass
    
    try:
        dt = datetime.strptime(date_str, '%Y-%m')
        if is_end:
            if dt.month == 12:
                return datetime(dt.year + 1, 1, 1)
            else:
                return datetime(dt.year, dt.month + 1, 1)
        return dt
    except ValueError:
        pass
    return None


# ── API: Expenses ────────────────────────────────────────────────────────────

def _filtered_user_expenses(db: Session):
    """Current user's expenses with from/to/card/tag/search filters applied,
    newest first. Shared by the list and CSV-export endpoints so they can't drift.
    """
    query = (
        db.query(Expense)
        .filter(Expense.user_id == current_user.id)
        .order_by(Expense.date.desc(), Expense.id.desc())
    )

    dt_from = parse_date_filter(request.args.get('from'), is_end=False)
    if dt_from:
        query = query.filter(Expense.date >= dt_from)

    dt_to = parse_date_filter(request.args.get('to'), is_end=True)
    if dt_to:
        query = query.filter(Expense.date < dt_to)

    card_filter = request.args.get('card')
    if card_filter:
        query = query.filter(Expense.card_number == card_filter)

    tag_filter = request.args.get('tag')
    if tag_filter:
        tag = db.query(Tag).filter_by(name=tag_filter, user_id=current_user.id).first()
        if tag:
            expense_ids = [
                et.expense_id for et in
                db.query(ExpenseTag.expense_id)
                  .filter_by(tag_id=tag.id)
                  .filter(ExpenseTag.source != 'suppressed')
                  .all()
            ]
            query = query.filter(Expense.id.in_(expense_ids)) if expense_ids else query.filter(False)

    search = request.args.get('search')
    if search and len(search) <= 200:
        query = query.filter(Expense.description.ilike(f'%{search}%'))

    return query


@app.route('/api/expenses')
@with_db
def api_expenses(db: Session):
    # Pagination
    page = max(1, request.args.get('page', 1, type=int))
    per_page = min(200, max(1, request.args.get('per_page', 50, type=int)))

    query = _filtered_user_expenses(db)

    # Count and paginate
    total = query.count()
    total_pages = max(1, (total + per_page - 1) // per_page)
    expenses = query.offset((page - 1) * per_page).limit(per_page).all()

    # Build response with tags
    items = []
    for exp in expenses:
        tags = (
            db.query(Tag.id, Tag.name, Tag.color, Tag.icon, ExpenseTag.source)
            .join(ExpenseTag, Tag.id == ExpenseTag.tag_id)
            .filter(ExpenseTag.expense_id == exp.id)
            .all()
        )
        # Mask card number for display (show last 4 only)
        masked_card = f"****{exp.card_number[-4:]}" if exp.card_number and len(exp.card_number) >= 4 else exp.card_number

        items.append({
            'id': exp.id,
            'date': exp.date.strftime('%Y-%m-%d'),
            'description': exp.description,
            'amount': exp.amount,
            'currency': exp.currency,
            'card_number': masked_card,
            'tags': [{'id': t[0], 'name': t[1], 'color': t[2], 'icon': t[3], 'source': t[4]} for t in tags],
        })

    return jsonify({
        'items': items,
        'page': page,
        'per_page': per_page,
        'total': total,
        'total_pages': total_pages,
    })


# ── API: CSV export ──────────────────────────────────────────────────────────

def _csv_safe(value: str) -> str:
    """Neutralise spreadsheet formula injection in text fields."""
    s = '' if value is None else str(value)
    if s and s[0] in ('=', '+', '-', '@', '\t', '\r'):
        return "'" + s
    return s


@app.route('/api/expenses/export.csv')
@with_db
def api_export_csv(db: Session):
    """Download the current user's expenses (respecting filters) as CSV."""
    import csv
    import io

    expenses = _filtered_user_expenses(db).all()

    # Preload tag names per expense in one query (avoid N+1).
    tags_by_expense = {}
    expense_ids = [e.id for e in expenses]
    if expense_ids:
        rows = (
            db.query(ExpenseTag.expense_id, Tag.name)
            .join(Tag, Tag.id == ExpenseTag.tag_id)
            .filter(ExpenseTag.expense_id.in_(expense_ids))
            .filter(ExpenseTag.source != 'suppressed')
            .all()
        )
        for expense_id, name in rows:
            tags_by_expense.setdefault(expense_id, []).append(name)

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['date', 'description', 'amount', 'currency', 'card', 'tags'])
    for exp in expenses:
        masked = f"****{exp.card_number[-4:]}" if exp.card_number and len(exp.card_number) >= 4 else (exp.card_number or '')
        writer.writerow([
            exp.date.strftime('%Y-%m-%d'),
            _csv_safe(exp.description),
            f"{exp.amount:.2f}",                      # numeric — keep sign, don't prefix
            exp.currency or 'TRY',
            masked,
            _csv_safe(';'.join(sorted(tags_by_expense.get(exp.id, [])))),
        ])

    return Response(
        output.getvalue(),
        mimetype='text/csv',
        headers={'Content-Disposition': 'attachment; filename=expenses.csv'},
    )


# ── API: Monthly Summary ────────────────────────────────────────────────────

@app.route('/api/summary/monthly')
@with_db
def api_summary_monthly(db: Session):
    """Monthly spending aggregation grouped by statement period (from source PDF).

    Each expense has a `statement_period` column (YYYY-MM) set during import,
    based on which PDF statement it was parsed from.  This matches the bank's
    actual billing cycle rather than guessing from the transaction date.

    Response includes breakdowns by currency and card number.
    """
    # Optional filters to limit which statement periods to return
    period_from = request.args.get('from')   # e.g. "2025-05"
    period_to = request.args.get('to')       # e.g. "2026-05"

    # Base query: exclude payments, include refunds (negative amounts)
    query = (
        db.query(Expense)
        .filter(Expense.user_id == current_user.id)
        .filter(Expense.category != 'Payment')
        .filter(Expense.statement_period.isnot(None))
    )
    if period_from:
        query = query.filter(Expense.statement_period >= period_from)
    if period_to:
        query = query.filter(Expense.statement_period <= period_to)

    expenses = query.all()

    # Get tag assignments for these expenses
    expense_ids = [e.id for e in expenses]
    tag_assignments = {}
    if expense_ids:
        tag_rows = (
            db.query(ExpenseTag.expense_id, Tag.name, Tag.color)
            .join(Tag, Tag.id == ExpenseTag.tag_id)
            .filter(ExpenseTag.expense_id.in_(expense_ids))
            .filter(ExpenseTag.source != 'suppressed')
            .all()
        )
        for row in tag_rows:
            if row.expense_id not in tag_assignments:
                tag_assignments[row.expense_id] = []
            tag_assignments[row.expense_id].append((row.name, row.color))

    # Aggregate by statement_period with breakdowns
    monthly = {}       # {period: {'total': float, 'count': int}}
    tag_monthly = {}   # {period: {tag_name: {'total': float, 'color': str}}}
    currency_monthly = {}  # {period: {currency: {'total': float, 'count': int}}}
    card_monthly = {}      # {period: {card_number: {'total': float, 'count': int, 'currency': str}}}

    for expense in expenses:
        period = expense.statement_period  # e.g. "2026-04"
        cur = expense.currency or 'TRY'
        card = expense.card_number or 'unknown'

        # Overall total (TRY only for main chart)
        if cur == 'TRY':
            if period not in monthly:
                monthly[period] = {'total': 0.0, 'count': 0}
            monthly[period]['total'] += expense.amount
            monthly[period]['count'] += 1

        # Per-currency breakdown
        if period not in currency_monthly:
            currency_monthly[period] = {}
        if cur not in currency_monthly[period]:
            currency_monthly[period][cur] = {'total': 0.0, 'count': 0}
        currency_monthly[period][cur]['total'] += expense.amount
        currency_monthly[period][cur]['count'] += 1

        # Per-card breakdown
        if period not in card_monthly:
            card_monthly[period] = {}
        card_key = f"{card} ({cur})"
        if card_key not in card_monthly[period]:
            card_monthly[period][card_key] = {'total': 0.0, 'count': 0, 'currency': cur, 'card_number': card}
        card_monthly[period][card_key]['total'] += expense.amount
        card_monthly[period][card_key]['count'] += 1

        # Per-tag breakdown (TRY only)
        if cur == 'TRY':
            for tag_name, tag_color in tag_assignments.get(expense.id, []):
                if period not in tag_monthly:
                    tag_monthly[period] = {}
                if tag_name not in tag_monthly[period]:
                    tag_monthly[period][tag_name] = {'total': 0.0, 'color': tag_color}
                tag_monthly[period][tag_name]['total'] += expense.amount

    # Collect all periods (from both TRY and non-TRY)
    all_periods = set(monthly.keys()) | set(currency_monthly.keys())

    # Build sorted result
    result = []
    for period in sorted(all_periods):
        parts = period.split('-')
        year, month = int(parts[0]), int(parts[1])
        by_tag = {}
        for tag_name, data in tag_monthly.get(period, {}).items():
            by_tag[tag_name] = {
                'total': round(data['total'], 2),
                'color': data['color'],
            }
        by_currency = {}
        for cur, data in currency_monthly.get(period, {}).items():
            by_currency[cur] = {
                'total': round(data['total'], 2),
                'count': data['count'],
            }
        by_card = []
        for card_key in sorted(card_monthly.get(period, {}).keys()):
            data = card_monthly[period][card_key]
            by_card.append({
                'card_number': data['card_number'],
                'currency': data['currency'],
                'total': round(data['total'], 2),
                'count': data['count'],
            })
        m = monthly.get(period, {'total': 0.0, 'count': 0})
        result.append({
            'year': year,
            'month': month,
            'label': period,
            'total': round(m['total'], 2),
            'count': m['count'],
            'by_tag': by_tag,
            'by_currency': by_currency,
            'by_card': by_card,
        })

    return jsonify(result)


# ── API: Tags ────────────────────────────────────────────────────────────────

@app.route('/api/tags')
@with_db
def api_tags(db: Session):
    tags = db.query(Tag).filter_by(user_id=current_user.id).order_by(Tag.name).all()
    return jsonify([t.to_dict() for t in tags])


@app.route('/api/tags', methods=['POST'])
@with_db
def api_create_tag(db: Session):
    data = request.get_json()
    if not data or not data.get('name'):
        return jsonify({'error': 'name is required'}), 400

    name = data['name'].strip().lower()[:50]
    if not name:
        return jsonify({'error': 'name cannot be empty'}), 400

    existing = db.query(Tag).filter_by(name=name, user_id=current_user.id).first()
    if existing:
        # Update
        if data.get('color'):
            existing.color = data['color'][:7]
        if data.get('icon'):
            existing.icon = data['icon'][:4]
        db.commit()
        return jsonify(existing.to_dict())

    tag = Tag(
        user_id=current_user.id,
        name=name,
        color=data.get('color', '#6366f1')[:7],
        icon=data.get('icon', '🏷️')[:4],
        is_default=False,
    )
    db.add(tag)
    db.commit()
    return jsonify(tag.to_dict()), 201


@app.route('/api/tags/<int:tag_id>', methods=['DELETE'])
@with_db
def api_delete_tag(db: Session, tag_id: int):
    tag = db.query(Tag).filter_by(id=tag_id, user_id=current_user.id).first()
    if not tag:
        return jsonify({'error': 'Tag not found'}), 404

    db.delete(tag)
    db.commit()
    return jsonify({'ok': True})


@app.route('/api/tags/<int:tag_id>', methods=['PUT'])
@with_db
def api_update_tag(db: Session, tag_id: int):
    tag = db.query(Tag).filter_by(id=tag_id, user_id=current_user.id).first()
    if not tag:
        return jsonify({'error': 'Tag not found'}), 404

    data = request.get_json()
    if not data:
        return jsonify({'error': 'No data provided'}), 400

    if 'name' in data:
        name = data['name'].strip().lower()[:50]
        if not name:
            return jsonify({'error': 'name cannot be empty'}), 400
        # Check uniqueness (within this user's tags)
        existing = db.query(Tag).filter_by(name=name, user_id=current_user.id).first()
        if existing and existing.id != tag_id:
            return jsonify({'error': 'Tag name already exists'}), 400
        tag.name = name
    
    if 'color' in data:
        tag.color = data['color'][:7]
    if 'icon' in data:
        tag.icon = data['icon'][:4]

    db.commit()
    return jsonify(tag.to_dict())


@app.route('/api/tags/<int:tag_id>/merge', methods=['POST'])
@with_db
def api_merge_tag(db: Session, tag_id: int):
    data = request.get_json()
    target_id = data.get('target_tag_id') if data else None
    
    if not target_id:
        return jsonify({'error': 'target_tag_id is required'}), 400
        
    if tag_id == target_id:
        return jsonify({'error': 'Cannot merge a tag into itself'}), 400

    source_tag = db.query(Tag).filter_by(id=tag_id, user_id=current_user.id).first()
    target_tag = db.query(Tag).filter_by(id=target_id, user_id=current_user.id).first()

    if not source_tag or not target_tag:
        return jsonify({'error': 'Source or target tag not found'}), 404

    # 1. Update rules: point all rules from source to target
    rules = db.query(TagRule).filter_by(tag_id=source_tag.id).all()
    for rule in rules:
        rule.tag_id = target_tag.id

    # 2. Update expenses: point ExpenseTags to target, avoiding duplicates
    source_ets = db.query(ExpenseTag).filter_by(tag_id=source_tag.id).all()
    target_expense_ids = {
        et.expense_id for et in db.query(ExpenseTag).filter_by(tag_id=target_tag.id).all()
    }

    for et in source_ets:
        if et.expense_id in target_expense_ids:
            # Expense already has target tag, just delete the source tag link
            db.delete(et)
        else:
            # Expense doesn't have target tag, update the link
            et.tag_id = target_tag.id

    # 3. Delete source tag
    db.delete(source_tag)
    db.commit()

    return jsonify({'ok': True})




# ── API: Tag Rules ───────────────────────────────────────────────────────────

@app.route('/api/tag-rules')
@with_db
def api_tag_rules(db: Session):
    rules = (
        db.query(TagRule)
        .filter_by(user_id=current_user.id)
        .order_by(TagRule.priority.desc(), TagRule.id.asc())
        .all()
    )
    return jsonify([r.to_dict() for r in rules])


@app.route('/api/tag-rules', methods=['POST'])
@with_db
def api_create_tag_rule(db: Session):
    data = request.get_json()
    if not data:
        return jsonify({'error': 'JSON body required'}), 400

    tag_id = data.get('tag_id')
    pattern = data.get('pattern', '').strip()[:200]
    match_type = data.get('match_type', 'contains')

    if not tag_id or not pattern:
        return jsonify({'error': 'tag_id and pattern are required'}), 400

    if match_type not in ('contains', 'starts_with', 'regex'):
        return jsonify({'error': 'match_type must be contains, starts_with, or regex'}), 400

    tag = db.query(Tag).filter_by(id=tag_id, user_id=current_user.id).first()
    if not tag:
        return jsonify({'error': 'Tag not found'}), 404

    priority = data.get('priority', 100)  # user rules default to 100

    rule = TagRule(
        user_id=current_user.id,
        tag_id=tag_id,
        pattern=pattern,
        match_type=match_type,
        priority=priority,
        is_default=False,
    )
    db.add(rule)
    db.commit()
    return jsonify(rule.to_dict()), 201


@app.route('/api/tag-rules/<int:rule_id>', methods=['DELETE'])
@with_db
def api_delete_tag_rule(db: Session, rule_id: int):
    rule = db.query(TagRule).filter_by(id=rule_id, user_id=current_user.id).first()
    if not rule:
        return jsonify({'error': 'Rule not found'}), 404

    db.delete(rule)
    db.commit()
    return jsonify({'ok': True})


# ── API: Expense Tagging ─────────────────────────────────────────────────────

@app.route('/api/expenses/<int:expense_id>/tags', methods=['POST'])
@with_db
def api_set_expense_tags(db: Session, expense_id: int):
    """Manually set tags on an expense. Replaces manual tags, keeps auto tags."""
    expense = db.query(Expense).filter_by(id=expense_id, user_id=current_user.id).first()
    if not expense:
        return jsonify({'error': 'Expense not found'}), 404

    data = request.get_json()
    if not data or 'tag_ids' not in data:
        return jsonify({'error': 'tag_ids list required'}), 400

    tag_ids = data['tag_ids']
    if not isinstance(tag_ids, list):
        return jsonify({'error': 'tag_ids must be a list'}), 400

    # Remove existing manual tags
    db.query(ExpenseTag).filter_by(expense_id=expense_id, source='manual').delete()

    # Add new manual tags
    for tid in tag_ids:
        if not isinstance(tid, int):
            continue
        tag = db.query(Tag).filter_by(id=tid, user_id=current_user.id).first()
        if not tag:
            continue
        # Check if auto-tag already exists for this tag
        existing = db.query(ExpenseTag).filter_by(expense_id=expense_id, tag_id=tid).first()
        if existing:
            existing.source = 'manual'  # upgrade from auto to manual
        else:
            et = ExpenseTag(expense_id=expense_id, tag_id=tid, source='manual')
            db.add(et)

    db.commit()
    return jsonify({'ok': True})


@app.route('/api/expenses/<int:expense_id>/tags/<int:tag_id>', methods=['DELETE'])
@with_db
def api_remove_expense_tag(db: Session, expense_id: int, tag_id: int):
    """
    Remove a tag from an expense.
    Instead of deleting the ExpenseTag, we mark it as 'suppressed' so that
    it is not automatically re-applied by TagEngine rules in the future.
    """
    if not _owns_expense(db, expense_id):
        return jsonify({'error': 'Expense not found'}), 404
    et = db.query(ExpenseTag).filter_by(expense_id=expense_id, tag_id=tag_id).first()
    if et:
        et.source = 'suppressed'
        db.commit()

    return jsonify({'ok': True})


@app.route('/api/expenses/<int:expense_id>/tags/<int:tag_id>/restore', methods=['POST'])
@with_db
def api_restore_expense_tag(db: Session, expense_id: int, tag_id: int):
    """
    Restore a suppressed tag by marking it as manual.
    """
    if not _owns_expense(db, expense_id):
        return jsonify({'error': 'Expense not found'}), 404
    et = db.query(ExpenseTag).filter_by(expense_id=expense_id, tag_id=tag_id).first()
    if et and et.source == 'suppressed':
        et.source = 'manual'
        db.commit()
        return jsonify({'ok': True})
    return jsonify({'error': 'Tag not found or not suppressed'}), 404


@app.route('/api/expenses/<int:expense_id>/tags/<int:tag_id>/hard', methods=['DELETE'])
@with_db
def api_hard_remove_expense_tag(db: Session, expense_id: int, tag_id: int):
    """Permanently delete a tag from an expense (hard delete)."""
    if not _owns_expense(db, expense_id):
        return jsonify({'error': 'Expense not found'}), 404
    et = db.query(ExpenseTag).filter_by(expense_id=expense_id, tag_id=tag_id).first()
    if et:
        db.delete(et)
        db.commit()
    return jsonify({'ok': True})


@app.route('/api/expenses/<int:expense_id>/details', methods=['GET'])
@with_db
def api_expense_details(db: Session, expense_id: int):
    """Get expense details including matching rules for auto-tags."""
    expense = db.query(Expense).filter_by(id=expense_id, user_id=current_user.id).first()
    if not expense:
        return jsonify({'error': 'Expense not found'}), 404

    # Get active and suppressed tags
    tags_query = (
        db.query(Tag.id, Tag.name, Tag.color, Tag.icon, ExpenseTag.source)
        .join(ExpenseTag, Tag.id == ExpenseTag.tag_id)
        .filter(ExpenseTag.expense_id == expense.id)
        .all()
    )

    # Find matching rules dynamically using TagEngine
    engine = TagEngine(db, current_user.id)
    matching_rules = engine.get_all_matching_rules(expense)
    rule_map = {rule.tag_id: rule for rule in matching_rules}

    tags_list = []
    for t in tags_query:
        tag_data = {
            'id': t[0],
            'name': t[1],
            'color': t[2],
            'icon': t[3],
            'source': t[4],
            'rule_id': None,
            'rule_pattern': None,
            'rule_match_type': None
        }
        
        # Show rule if it matches
        rule = rule_map.get(t[0])
        if rule:
            tag_data['rule_id'] = rule.id
            tag_data['rule_pattern'] = rule.pattern
            tag_data['rule_match_type'] = rule.match_type
            
        tags_list.append(tag_data)

    masked_card = f"****{expense.card_number[-4:]}" if expense.card_number and len(expense.card_number) >= 4 else expense.card_number

    return jsonify({
        'id': expense.id,
        'date': expense.date.strftime('%Y-%m-%d'),
        'description': expense.description,
        'amount': expense.amount,
        'currency': expense.currency,
        'card_number': masked_card,
        'tags': tags_list
    })


# ── API: Re-tag ──────────────────────────────────────────────────────────────

@app.route('/api/retag', methods=['POST'])
@with_db
def api_retag(db: Session):
    engine = TagEngine(db, current_user.id)
    applied = engine.retag_all()
    stats = engine.get_stats()
    return jsonify({
        'applied': applied,
        'stats': stats,
    })


# ── API: Cards ───────────────────────────────────────────────────────────────

@app.route('/api/cards')
@with_db
def api_cards(db: Session):
    query = (
        db.query(Expense.card_number, func.count(Expense.id))
        .filter(Expense.user_id == current_user.id)
        .filter(Expense.card_number.isnot(None))
        .filter(Expense.card_number != '')
    )

    date_from = request.args.get('from')
    date_to = request.args.get('to')
    dt_from = parse_date_filter(date_from, is_end=False)
    if dt_from:
        query = query.filter(Expense.date >= dt_from)
        
    dt_to = parse_date_filter(date_to, is_end=True)
    if dt_to:
        query = query.filter(Expense.date < dt_to)

    cards = (
        query
        .group_by(Expense.card_number)
        .order_by(func.count(Expense.id).desc())
        .all()
    )
    return jsonify([
        {
            'card_number': c[0],
            'masked': f"****{c[0][-4:]}" if c[0] and len(c[0]) >= 4 else c[0],
            'count': c[1],
        }
        for c in cards
    ])

