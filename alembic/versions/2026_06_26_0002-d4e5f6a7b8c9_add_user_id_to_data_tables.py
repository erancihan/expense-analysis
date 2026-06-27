"""add user_id to data tables (multi-tenant isolation)

Adds a non-null user_id FK to expenses, tags, tag_rules and processed_emails,
backfilling existing rows to an owner, and makes tag names unique per-user
instead of globally.

Backfill owner resolution:
  1. the first existing user (by id), else
  2. if data exists but there are no users, a default admin is created from
     EXPENSE_ADMIN_EMAIL / EXPENSE_ADMIN_PASSWORD (defaults: admin@localhost /
     "changeme") — RESET ITS PASSWORD afterwards via scripts/create_user.py.

Revision ID: d4e5f6a7b8c9
Revises: c3f1a9d7b2e4
Create Date: 2026-06-26 00:02:00.000000
"""
import logging
import os
from datetime import datetime, timezone
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd4e5f6a7b8c9'
down_revision: Union[str, Sequence[str], None] = 'c3f1a9d7b2e4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

log = logging.getLogger('alembic.runtime.migration')

_DATA_TABLES = ['expenses', 'tags', 'tag_rules', 'processed_emails']
_NAMING = {"uq": "uq_%(table_name)s_%(column_0_name)s"}


def _resolve_owner_id(bind) -> Union[int, None]:
    """Return the user id to assign existing rows to, creating one if needed."""
    row = bind.execute(sa.text("SELECT id FROM users ORDER BY id LIMIT 1")).fetchone()
    if row:
        return row[0]

    # No users yet — only create a placeholder admin if there's data to own.
    has_data = any(
        bind.execute(sa.text(f"SELECT 1 FROM {t} LIMIT 1")).fetchone() is not None
        for t in _DATA_TABLES
    )
    if not has_data:
        return None

    from werkzeug.security import generate_password_hash
    email = os.environ.get('EXPENSE_ADMIN_EMAIL', 'admin@localhost')
    password = os.environ.get('EXPENSE_ADMIN_PASSWORD', 'changeme')
    bind.execute(
        sa.text(
            "INSERT INTO users (email, password_hash, is_admin, is_active, created_at) "
            "VALUES (:email, :ph, 1, 1, :ts)"
        ),
        {
            'email': email,
            'ph': generate_password_hash(password),
            'ts': datetime.now(timezone.utc).replace(tzinfo=None),
        },
    )
    log.warning(
        "No user existed; created default admin '%s' to own pre-existing data. "
        "RESET ITS PASSWORD with scripts/create_user.py before exposing the app.",
        email,
    )
    row = bind.execute(
        sa.text("SELECT id FROM users WHERE email = :email"), {'email': email}
    ).fetchone()
    return row[0]


def upgrade() -> None:
    """Upgrade schema."""
    bind = op.get_bind()

    # 1. Add nullable user_id columns.
    for table in _DATA_TABLES:
        with op.batch_alter_table(table) as batch_op:
            batch_op.add_column(sa.Column('user_id', sa.Integer(), nullable=True))

    # 2. Resolve the owner and backfill existing rows.
    owner_id = _resolve_owner_id(bind)
    if owner_id is not None:
        for table in _DATA_TABLES:
            bind.execute(
                sa.text(f"UPDATE {table} SET user_id = :uid WHERE user_id IS NULL"),
                {'uid': owner_id},
            )

    # 3. Make user_id non-null + add FK. Swap the global tag-name unique
    #    constraint for a per-user one (naming_convention lets batch find the
    #    existing unnamed UNIQUE('name') created by the init migration).
    for table in ['expenses', 'tag_rules', 'processed_emails']:
        with op.batch_alter_table(table) as batch_op:
            batch_op.alter_column('user_id', existing_type=sa.Integer(), nullable=False)
            batch_op.create_foreign_key(f'fk_{table}_user_id_users', 'users', ['user_id'], ['id'])

    with op.batch_alter_table('tags', naming_convention=_NAMING) as batch_op:
        batch_op.alter_column('user_id', existing_type=sa.Integer(), nullable=False)
        batch_op.create_foreign_key('fk_tags_user_id_users', 'users', ['user_id'], ['id'])
        batch_op.drop_constraint('uq_tags_name', type_='unique')
        batch_op.create_unique_constraint('uq_tags_user_name', ['user_id', 'name'])


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('tags', naming_convention=_NAMING) as batch_op:
        batch_op.drop_constraint('uq_tags_user_name', type_='unique')
        batch_op.create_unique_constraint('uq_tags_name', ['name'])
        batch_op.drop_column('user_id')

    for table in ['expenses', 'tag_rules', 'processed_emails']:
        with op.batch_alter_table(table) as batch_op:
            batch_op.drop_column('user_id')
