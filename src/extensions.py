"""
Flask extensions, defined unbound so both src.web (which calls init_app) and
src.auth (which decorates routes) can import them without a circular import.
"""

import logging
from functools import wraps

from flask import jsonify
from flask_wtf import CSRFProtect
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from sqlalchemy.orm import Session

from src.database import SessionLocal

logger = logging.getLogger(__name__)

# CSRF protection for all state-changing (POST/PUT/PATCH/DELETE) requests.
# The SPA sends the token via the X-CSRFToken header; forms via a hidden field.
csrf = CSRFProtect()

# Rate limiter, keyed on client IP. No global default limit — specific limits
# are applied per route (e.g. login). In-memory storage is fine for a single
# process; point storage_uri at redis for multi-worker deployments.
limiter = Limiter(key_func=get_remote_address)


# ── DB session helper ────────────────────────────────────────────────────────
# Lives here rather than in src.web so blueprints can use it: src.web imports
# every blueprint, so a blueprint importing src.web would be circular.

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
