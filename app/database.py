"""
Database connection and session management.

Uses SQLite for local persistence so schedule history accumulates across XML
uploads. Upload once (or a few times) and predictions continue on return visits
without re-importing the same files.

Configure persistence location:
  - DATA_DIR=/path/to/folder  (default: ./data)
  - DATABASE_URL=sqlite:////absolute/path/to/captain_scheduler.db  (overrides DATA_DIR)
"""

import os
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

# Persistent storage directory — mount this volume in Docker/cloud deployments.
DATA_DIR = Path(os.environ.get("DATA_DIR", "data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)

DEFAULT_DB_PATH = DATA_DIR / "captain_scheduler.db"
DATABASE_URL = os.environ.get("DATABASE_URL", f"sqlite:///{DEFAULT_DB_PATH.resolve()}")

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False},  # Required for SQLite + FastAPI threading
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    """SQLAlchemy declarative base for all ORM models."""


def get_database_path() -> Path:
    """Return the filesystem path to the SQLite database file."""
    url = DATABASE_URL
    if url.startswith("sqlite:///"):
        raw = url.replace("sqlite:///", "", 1)
        return Path(raw)
    return DEFAULT_DB_PATH


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
