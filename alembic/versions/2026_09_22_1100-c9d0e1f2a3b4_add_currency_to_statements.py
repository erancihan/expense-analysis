"""add currency to statements

Revision ID: c9d0e1f2a3b4
Revises: b8c9d0e1f2a3
Create Date: 2026-09-22 11:00:00.000000

İşbank sends one statement per currency for the same card and cycle: a TRY
and a USD statement share card_number and closing_date. The first backfill dry
run treated eight such USD/TRY pairs as re-deliveries of one statement and left
their rows unlinked, so currency joins the statement's identity.

Existing statement rows get 'TRY'. Re-running scripts/backfill_statements.py
creates the USD statements and links their rows to them.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'c9d0e1f2a3b4'
down_revision: Union[str, Sequence[str], None] = 'b8c9d0e1f2a3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('statements') as batch_op:
        batch_op.add_column(sa.Column('currency', sa.String(length=3), nullable=False,
                                      server_default='TRY'))
        batch_op.drop_constraint('uq_statements_user_card_closing', type_='unique')
        batch_op.create_unique_constraint(
            'uq_statements_user_card_closing_currency',
            ['user_id', 'card_number', 'closing_date', 'currency'])


def downgrade() -> None:
    with op.batch_alter_table('statements') as batch_op:
        batch_op.drop_constraint('uq_statements_user_card_closing_currency', type_='unique')
        batch_op.create_unique_constraint(
            'uq_statements_user_card_closing', ['user_id', 'card_number', 'closing_date'])
        batch_op.drop_column('currency')
