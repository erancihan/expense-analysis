from sqlalchemy import (
    Column, Integer, String, Float, DateTime, Text, Boolean,
    ForeignKey, UniqueConstraint, create_engine,
)
from sqlalchemy.orm import declarative_base, relationship
from datetime import datetime, timezone

from flask_login import UserMixin

def utc_now():
    return datetime.now(timezone.utc).replace(tzinfo=None)

Base = declarative_base()


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
    gmail_token = Column(Text, nullable=True)
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
    created_at = Column(DateTime, default=utc_now)

    # Many-to-many relationship with Tag through ExpenseTag
    expense_tags = relationship('ExpenseTag', back_populates='expense', cascade='all, delete-orphan')

    def __repr__(self):
        return f"<Expense(date='{self.date}', description='{self.description}', amount={self.amount})>"


class Tag(Base):
    __tablename__ = 'tags'

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id'), nullable=False, index=True)
    name = Column(String, nullable=False)                    # "restaurant", "grocery"
    color = Column(String, default='#6366f1')                # hex color for UI chip
    icon = Column(String, default='🏷️')                      # emoji
    is_default = Column(Boolean, default=False)              # shipped with app
    created_at = Column(DateTime, default=utc_now)

    # Relationships
    expense_tags = relationship('ExpenseTag', back_populates='tag', cascade='all, delete-orphan')
    rules = relationship('TagRule', back_populates='tag', cascade='all, delete-orphan')

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

