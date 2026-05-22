from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from src.models import Base
import os

# Default to a local SQLite file in the data directory
DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data', 'expenses.db')
DATABASE_URL = os.getenv('DATABASE_URL', f'sqlite:///{DB_PATH}')

engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False} if 'sqlite' in DATABASE_URL else {})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

def init_db():
    """Initialize database connection. Schema is managed by Alembic migrations."""
    # Schema managed by Alembic — run `make migrations` to apply.
    # Do NOT call Base.metadata.create_all() here.
    pass

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
