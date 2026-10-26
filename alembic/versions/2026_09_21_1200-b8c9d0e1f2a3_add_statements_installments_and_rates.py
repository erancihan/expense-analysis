"""add statements, installment columns on expenses, and rates

Revision ID: b8c9d0e1f2a3
Revises: a1b2c3d4e5f6
Create Date: 2026-09-21 12:00:00.000000

SQLite cannot add a foreign key to an existing table, so the expenses change
uses op.batch_alter_table, matching
2026_08_06_1200-a1b2c3d4e5f6_add_parent_id_to_tags.py. The new columns are
nullable: existing rows are filled by scripts/backfill_statements.py.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'b8c9d0e1f2a3'
down_revision: Union[str, Sequence[str], None] = 'a1b2c3d4e5f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_NAMING = {'fk': 'fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s'}


def upgrade() -> None:
    op.create_table(
        'statements',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id'), nullable=False),
        sa.Column('card_number', sa.String(), nullable=False),
        sa.Column('closing_date', sa.Date(), nullable=False),
        sa.Column('period', sa.String(length=7), nullable=False),
        sa.Column('source_file', sa.String(), nullable=True),
        sa.Column('email_id', sa.String(), nullable=True),
        sa.Column('imported_at', sa.DateTime(), nullable=True),
        sa.UniqueConstraint('user_id', 'card_number', 'closing_date',
                            name='uq_statements_user_card_closing'),
    )
    op.create_index('ix_statements_user_id', 'statements', ['user_id'])

    with op.batch_alter_table('expenses', naming_convention=_NAMING) as batch_op:
        batch_op.add_column(sa.Column('statement_id', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('installment_no', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('installment_count', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('installment_total', sa.Float(), nullable=True))
        batch_op.create_foreign_key(
            'fk_expenses_statement_id_statements', 'statements', ['statement_id'], ['id'])
        batch_op.create_index('ix_expenses_statement_id', ['statement_id'])

    op.create_table(
        'rates',
        sa.Column('series', sa.String(), primary_key=True),
        sa.Column('month', sa.String(length=7), primary_key=True),
        sa.Column('value', sa.Float(), nullable=False),
        sa.Column('provisional', sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_table('rates')

    with op.batch_alter_table('expenses', naming_convention=_NAMING) as batch_op:
        batch_op.drop_index('ix_expenses_statement_id')
        batch_op.drop_constraint('fk_expenses_statement_id_statements', type_='foreignkey')
        batch_op.drop_column('installment_total')
        batch_op.drop_column('installment_count')
        batch_op.drop_column('installment_no')
        batch_op.drop_column('statement_id')

    op.drop_index('ix_statements_user_id', table_name='statements')
    op.drop_table('statements')
