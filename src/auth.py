"""
Authentication: login / logout routes and helpers.

Password accounts (hashed with werkzeug) backed by Flask-Login sessions. There
is intentionally no public sign-up — accounts are created by an admin via
`scripts/create_user.py`. The LoginManager itself is initialised in src.web.
"""

import logging
from urllib.parse import urlparse

from flask import Blueprint, render_template, request, redirect, url_for
from flask_login import login_user, logout_user, login_required, current_user
from werkzeug.security import check_password_hash

from src.database import SessionLocal
from src.models import User

logger = logging.getLogger(__name__)

auth_bp = Blueprint('auth', __name__)


def _is_safe_next(target: str) -> bool:
    """Only allow same-host relative redirects (avoid open-redirect via ?next=)."""
    if not target:
        return False
    parsed = urlparse(target)
    return not parsed.scheme and not parsed.netloc and target.startswith('/')


@auth_bp.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('index'))

    if request.method == 'POST':
        email = (request.form.get('email') or '').strip().lower()
        password = request.form.get('password') or ''
        next_url = request.args.get('next') or request.form.get('next')

        db = SessionLocal()
        try:
            user = db.query(User).filter_by(email=email).first()
            if user and user.is_active and check_password_hash(user.password_hash, password):
                login_user(user)
                logger.info(f"Login success for {email}")
                if _is_safe_next(next_url):
                    return redirect(next_url)
                return redirect(url_for('index'))
        finally:
            db.close()

        logger.warning(f"Login failed for {email!r}")
        # Same generic message whether the email exists or not.
        return render_template('login.html', error='Invalid email or password', next=next_url), 401

    return render_template('login.html', next=request.args.get('next'))


@auth_bp.route('/logout', methods=['POST'])
@login_required
def logout():
    logout_user()
    return redirect(url_for('auth.login'))
