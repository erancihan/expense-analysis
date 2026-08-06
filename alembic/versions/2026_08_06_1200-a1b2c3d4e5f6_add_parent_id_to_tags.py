"""add parent_id to tags (two-level tag hierarchy)

Revision ID: a1b2c3d4e5f6
Revises: f6a7b8c9d0e1
Create Date: 2026-08-06 12:00:00.000000

SQLite cannot add a foreign key to an existing table, so this uses
op.batch_alter_table, matching the pattern in
2026_06_26_0002-d4e5f6a7b8c9_add_user_id_to_data_tables.py.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, Sequence[str], None] = 'f6a7b8c9d0e1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_NAMING = {'fk': 'fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s'}


def upgrade() -> None:
    with op.batch_alter_table('tags', naming_convention=_NAMING) as batch_op:
        batch_op.add_column(sa.Column('parent_id', sa.Integer(), nullable=True))
        batch_op.create_foreign_key('fk_tags_parent_id_tags', 'tags', ['parent_id'], ['id'])
        batch_op.create_index('ix_tags_parent_id', ['parent_id'])


def downgrade() -> None:
    with op.batch_alter_table('tags', naming_convention=_NAMING) as batch_op:
        batch_op.drop_index('ix_tags_parent_id')
        batch_op.drop_constraint('fk_tags_parent_id_tags', type_='foreignkey')
        batch_op.drop_column('parent_id')
