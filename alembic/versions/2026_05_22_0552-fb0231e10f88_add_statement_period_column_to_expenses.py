"""add statement_period column to expenses

Revision ID: fb0231e10f88
Revises: bd95b8341f8a
Create Date: 2026-05-22 05:52:16.335573

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'fb0231e10f88'
down_revision: Union[str, Sequence[str], None] = 'bd95b8341f8a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('expenses', sa.Column('statement_period', sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column('expenses', 'statement_period')
