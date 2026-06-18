"""
Database connection and session management.

Uses SQLite for local persistence so schedule history accumulates across XML
uploads. The database file (`captain_scheduler.db`) is created automatically
on first startup.
"""

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

# Local SQLite database file — swap this URL for PostgreSQL in production if needed.
DATABASE_URL = "sqlite:///./captain_scheduler.db"

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False},  # Required for SQLite + FastAPI threading
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    """SQLAlchemy declarative base for all ORM models."""


def get_db():
    """
    FastAPI dependency that yields a database session per request.

    Ensures the session is always closed after the request completes, even
    when an error occurs during route handling.
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    """
    Create all database tables if they do not already exist.

    Called once during application startup. Imports models so SQLAlchemy
    registers every table before `create_all` runs.
    """
    from app import models  # noqa: F401

    Base.metadata.create_all(bind=engine)
