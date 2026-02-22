from sqlalchemy import Column, Integer, String, Float, DateTime, Text, create_engine
from sqlalchemy.orm import declarative_base
from datetime import datetime

Base = declarative_base()

class Expense(Base):
    __tablename__ = 'expenses'

    id = Column(Integer, primary_key=True)
    date = Column(DateTime, nullable=False)
    description = Column(String, nullable=False)
    amount = Column(Float, nullable=False)
    currency = Column(String, default='TRY')
    category = Column(String, nullable=True)
    bank_source = Column(String, nullable=False)
    raw_text = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    def __repr__(self):
        return f"<Expense(date='{self.date}', description='{self.description}', amount={self.amount})>"

class ProcessedEmail(Base):
    __tablename__ = 'processed_emails'

    id = Column(Integer, primary_key=True)
    message_id = Column(String, unique=True, nullable=False)
    processed_at = Column(DateTime, default=datetime.utcnow)
    status = Column(String, default='SUCCESS') # SUCCESS, FAILED

    def __repr__(self):
        return f"<ProcessedEmail(message_id='{self.message_id}', status='{self.status}')>"
