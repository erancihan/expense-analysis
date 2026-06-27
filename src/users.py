"""
User resolution for CLI / ingestion entry points.

The web app always knows the user from the session (`current_user`). The CLI
tools and the Gmail pipeline don't, so they resolve an "owner" to attribute data
to: an explicit email when given, otherwise the sole/first admin (a personal
deployment normally has exactly one).
"""

import logging
from typing import Optional

from sqlalchemy.orm import Session

from src.models import User

logger = logging.getLogger(__name__)


class NoOwnerError(RuntimeError):
    """Raised when an owning user cannot be resolved."""


def resolve_owner(db: Session, email: Optional[str] = None) -> User:
    """Resolve the user that CLI-imported / processed data belongs to.

    With an email, returns that user (error if missing). Otherwise returns the
    first admin, or — if there are no admins — the only user. Ambiguity (no
    email, no admin, multiple users) is an error so data is never silently
    attributed to the wrong account.
    """
    if email:
        user = db.query(User).filter_by(email=email.strip().lower()).first()
        if not user:
            raise NoOwnerError(f"No user with email {email!r}. Create one with scripts/create_user.py.")
        return user

    admins = db.query(User).filter_by(is_admin=True).order_by(User.id).all()
    if len(admins) == 1:
        return admins[0]
    if len(admins) > 1:
        raise NoOwnerError(
            "Multiple admins exist; pass --email to choose which account owns the data."
        )

    users = db.query(User).order_by(User.id).all()
    if not users:
        raise NoOwnerError("No users exist. Create one with scripts/create_user.py.")
    if len(users) == 1:
        return users[0]
    raise NoOwnerError(
        "Multiple users and no admin; pass --email to choose which account owns the data."
    )
