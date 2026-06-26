from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from src.models import Base
# Single source of truth for the DB URL: src.config resolves env vars AND the
# optional config.local.yaml override. Re-exported here so alembic/env.py (which
# imports DATABASE_URL from this module) stays consistent with the app.
from src.config import DATABASE_URL

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
