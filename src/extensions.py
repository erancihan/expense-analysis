"""
Flask extensions, defined unbound so both src.web (which calls init_app) and
src.auth (which decorates routes) can import them without a circular import.
"""

from flask_wtf import CSRFProtect
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

# CSRF protection for all state-changing (POST/PUT/PATCH/DELETE) requests.
# The SPA sends the token via the X-CSRFToken header; forms via a hidden field.
csrf = CSRFProtect()

# Rate limiter, keyed on client IP. No global default limit — specific limits
# are applied per route (e.g. login). In-memory storage is fine for a single
# process; point storage_uri at redis for multi-worker deployments.
limiter = Limiter(key_func=get_remote_address)
