from sqlalchemy import (
    Column, Integer, String, Float, Date, DateTime, Text, Boolean,
    ForeignKey, UniqueConstraint, create_engine, TypeDecorator,
)
from sqlalchemy.orm import declarative_base, relationship
from datetime import datetime, timezone

from flask_login import UserMixin

import logging

from src import token_crypto

logger = logging.getLogger(__name__)

def utc_now():
    return datetime.now(timezone.utc).replace(tzinfo=None)

Base = declarative_base()


class EncryptedText(TypeDecorator):
    """A TEXT column encrypted at rest via src.token_crypto.

    Reads and writes are deliberately asymmetric.

    A failed read logs and returns None rather than raising. Flask-Login's
    user_loader (src/web.py:60-66) loads a User on every authenticated request,
    and loading decrypts this column — so raising here would turn a wrong or
    rotated key into a 500 on every page in the app, not just on Gmail features.
    Returning None degrades to "not connected" and the user reconnects.

    A failed write raises, because storing plaintext would defeat the purpose.
    SQLAlchemy wraps that as sqlalchemy.exc.StatementError, with the underlying
    TokenEncryptionError available as `err.orig`.
    """

    impl = Text
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        return token_crypto.encrypt(value)

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        try:
            return token_crypto.decrypt(value)
        except token_crypto.TokenEncryptionError as e:
            logger.error(f"Stored Gmail token could not be decrypted: {e}")
            return None


class User(Base, UserMixin):
    __tablename__ = 'users'

    id = Column(Integer, primary_key=True)
    email = Column(String, unique=True, nullable=False)
    password_hash = Column(String, nullable=False)
    is_admin = Column(Boolean, default=False)
    # NOTE: shadows UserMixin.is_active (a property) with a real column so
    # Flask-Login reads the stored flag. Disabled users can't authenticate.
    is_active = Column(Boolean, default=True, nullable=False)
    # Per-user Gmail OAuth token (JSON from the web consent flow). Null = not
    # connected. The Gmail processor ingests each connected user's statements
    # into their own account.
    gmail_token = Column(EncryptedText, nullable=True)
    created_at = Column(DateTime, default=utc_now)

    def __repr__(self):
        return f"<User(email='{self.email}')>"

    @property
    def gmail_connected(self) -> bool:
        return bool(self.gmail_token)


class Expense(Base):
    __tablename__ = 'expenses'

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id'), nullable=False, index=True)
    date = Column(DateTime, nullable=False)
    description = Column(String, nullable=False)
    amount = Column(Float, nullable=False)
    currency = Column(String, default='TRY')
    category = Column(String, nullable=True)
    bank_source = Column(String, nullable=False)
    card_number = Column(String, nullable=True)
    statement_period = Column(String, nullable=True)  # YYYY-MM from source PDF
    raw_text = Column(Text, nullable=True)
    # The statement this row was parsed from (NULL for rows imported before
    # statements were recorded and not yet backfilled).
    statement_id = Column(Integer, ForeignKey('statements.id'), nullable=True, index=True)
    # From the statement's TAKSİT BİLGİSİ column, "n/N taksidi (total)":
    # installment n of N of a purchase whose full amount is installment_total.
    installment_no = Column(Integer, nullable=True)
    installment_count = Column(Integer, nullable=True)
    installment_total = Column(Float, nullable=True)
    created_at = Column(DateTime, default=utc_now)

    # Many-to-many relationship with Tag through ExpenseTag
    expense_tags = relationship('ExpenseTag', back_populates='expense', cascade='all, delete-orphan')
    statement = relationship('Statement', back_populates='expenses')

    def __repr__(self):
        return f"<Expense(date='{self.date}', description='{self.description}', amount={self.amount})>"


class Statement(Base):
    """One imported card statement (one PDF).

    closing_date is the "Hesap Kesim Tarihi"; period is its YYYY-MM. Coverage
    analysis uses the closing dates to find statements that were never
    imported.
    """
    __tablename__ = 'statements'

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id'), nullable=False, index=True)
    card_number = Column(String, nullable=False)
    closing_date = Column(Date, nullable=False)
    # İşbank sends one statement per currency for the same card and cycle, so
    # a TRY and a USD statement share card_number and closing_date.
    currency = Column(String(3), nullable=False, default='TRY')
    period = Column(String(7), nullable=False)
    source_file = Column(String, nullable=True)
    email_id = Column(String, nullable=True)
    imported_at = Column(DateTime, default=utc_now)

    expenses = relationship('Expense', back_populates='statement')

    # A statement re-delivered by a second email is the same statement.
    __table_args__ = (
        UniqueConstraint('user_id', 'card_number', 'closing_date', 'currency',
                         name='uq_statements_user_card_closing_currency'),
    )

    def __repr__(self):
        return f"<Statement(card='{self.card_number}', closing_date='{self.closing_date}')>"


class Tag(Base):
    __tablename__ = 'tags'

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id'), nullable=False, index=True)
    name = Column(String, nullable=False)                    # "restaurant", "grocery"
    color = Column(String, default='#6366f1')                # hex color for UI chip
    icon = Column(String, default='🏷️')                      # emoji
    is_default = Column(Boolean, default=False)              # shipped with app
    # Two-level hierarchy: a parent tag has parent_id=None; a child points at it.
    # Depth is capped at 2 (validated in src/seeding.py and POST /api/tags) so
    # every rollup stays a single join.
    parent_id = Column(Integer, ForeignKey('tags.id'), nullable=True, index=True)
    created_at = Column(DateTime, default=utc_now)

    # Relationships
    expense_tags = relationship('ExpenseTag', back_populates='tag', cascade='all, delete-orphan')
    rules = relationship('TagRule', back_populates='tag', cascade='all, delete-orphan')
    # NOTE: deleting a parent takes its children — and, through their own
    # expense_tags cascade, every assignment those children carry. web.py's
    # DELETE /api/tags/<id> refuses to delete a tag that still has children.
    children = relationship(
        'Tag', back_populates='parent', cascade='all, delete-orphan',
    )
    parent = relationship('Tag', back_populates='children', remote_side=[id])

    # Tag names are unique per user, not globally.
    __table_args__ = (
        UniqueConstraint('user_id', 'name', name='uq_tags_user_name'),
    )

    def __repr__(self):
        return f"<Tag(name='{self.name}')>"

    def to_dict(self):
        return {
            'id': self.id,
            'name': self.name,
            'color': self.color,
            'icon': self.icon,
            'is_default': self.is_default,
            'parent_id': self.parent_id,
            'parent_name': self.parent.name if self.parent else None,
        }


class TagRule(Base):
    __tablename__ = 'tag_rules'

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id'), nullable=False, index=True)
    tag_id = Column(Integer, ForeignKey('tags.id'), nullable=False)
    pattern = Column(String, nullable=False)                 # "UBER", "YEMEKSEPETI"
    match_type = Column(String, default='contains')          # contains | starts_with | regex
    priority = Column(Integer, default=0)                    # higher = checked first
    is_default = Column(Boolean, default=False)              # shipped rule vs user rule
    created_at = Column(DateTime, default=utc_now)

    tag = relationship('Tag', back_populates='rules')

    def __repr__(self):
        return f"<TagRule(pattern='{self.pattern}', tag='{self.tag_id}')>"

    def to_dict(self):
        return {
            'id': self.id,
            'tag_id': self.tag_id,
            'tag_name': self.tag.name if self.tag else None,
            'pattern': self.pattern,
            'match_type': self.match_type,
            'priority': self.priority,
            'is_default': self.is_default,
        }


class ExpenseTag(Base):
    __tablename__ = 'expense_tags'

    id = Column(Integer, primary_key=True)
    expense_id = Column(Integer, ForeignKey('expenses.id'), nullable=False)
    tag_id = Column(Integer, ForeignKey('tags.id'), nullable=False)
    source = Column(String, default='auto')                  # auto | manual

    expense = relationship('Expense', back_populates='expense_tags')
    tag = relationship('Tag', back_populates='expense_tags')

    __table_args__ = (
        UniqueConstraint('expense_id', 'tag_id', name='uq_expense_tag'),
    )

    def __repr__(self):
        return f"<ExpenseTag(expense_id={self.expense_id}, tag_id={self.tag_id}, source='{self.source}')>"


class Budget(Base):
    __tablename__ = 'budgets'

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id'), nullable=False, index=True)
    tag_id = Column(Integer, ForeignKey('tags.id'), nullable=True)  # NULL = overall
    amount = Column(Float, nullable=False)                          # monthly limit (TRY)
    created_at = Column(DateTime, default=utc_now)

    tag = relationship('Tag')

    __table_args__ = (
        UniqueConstraint('user_id', 'tag_id', name='uq_budget_user_tag'),
    )

    def to_dict(self):
        return {
            'id': self.id,
            'tag_id': self.tag_id,
            'tag_name': self.tag.name if self.tag else None,
            'amount': self.amount,
        }


class ProcessedEmail(Base):
    __tablename__ = 'processed_emails'

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id'), nullable=False, index=True)
    # Gmail message ids are globally unique (each user connects their own Gmail),
    # so a plain unique constraint is sufficient; user_id scopes lookups.
    message_id = Column(String, unique=True, nullable=False)
    processed_at = Column(DateTime, default=utc_now)
    status = Column(String, default='SUCCESS') # SUCCESS, FAILED

    def __repr__(self):
        return f"<ProcessedEmail(message_id='{self.message_id}', status='{self.status}')>"



class Rate(Base):
    """Monthly macro series (USDTRY, EURTRY, CPI_TR) — see src/analytics/rates.py.

    Public figures shared by every user, so there is no user_id. provisional
    marks a month copied forward from the last published value.
    """
    __tablename__ = 'rates'

    series = Column(String, primary_key=True)
    month = Column(String(7), primary_key=True)   # YYYY-MM
    value = Column(Float, nullable=False)
    provisional = Column(Boolean, default=False, nullable=False)

    def __repr__(self):
        return f"<Rate(series='{self.series}', month='{self.month}', value={self.value})>"
