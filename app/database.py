"""
Database connection and session management.

Uses SQLite by default for local persistence. When DATABASE_URL is set
(e.g. postgresql://… in cloud deployments), that URL is used instead.

Configure persistence location:
  - DATA_DIR=/path/to/folder  (default: ./data) — SQLite only
  - DATABASE_URL=sqlite:////absolute/path/to/captain_scheduler.db
  - DATABASE_URL=postgresql://user:pass@host:5432/dbname
"""

import os
from pathlib import Path

from sqlalchemy import create_engine, extract, func, cast, Integer
from sqlalchemy.orm import DeclarativeBase, sessionmaker

# Persistent storage directory — mount this volume in Docker/cloud deployments.
DATA_DIR = Path(os.environ.get("DATA_DIR", "data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)

DEFAULT_DB_PATH = DATA_DIR / "captain_scheduler.db"


def _normalize_database_url(url: str) -> str:
    """
    Normalize DATABASE_URL for SQLAlchemy and ensure a Postgres driver is specified.

    Cloud platforms often provide postgres:// URLs without a driver name.
    We prefer psycopg (v3) via postgresql+psycopg://.
    """
    if url.startswith("postgres://"):
        url = "postgresql+psycopg://" + url[len("postgres://") :]
    elif url.startswith("postgresql://") and "+" not in url.split("://", 1)[0]:
        url = "postgresql+psycopg://" + url[len("postgresql://") :]
    return url


def _resolve_database_url() -> str:
    raw = os.environ.get("DATABASE_URL")
    if raw and raw.strip():
        return _normalize_database_url(raw.strip())
    return f"sqlite:///{DEFAULT_DB_PATH.resolve()}"


DATABASE_URL = _resolve_database_url()


def _engine_kwargs(url: str) -> dict:
    """Build dialect-specific create_engine options."""
    if url.startswith("sqlite:"):
        return {"connect_args": {"check_same_thread": False}}
    return {}


engine = create_engine(DATABASE_URL, **_engine_kwargs(DATABASE_URL))
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    """SQLAlchemy declarative base for all ORM models."""


def get_database_path() -> Path:
    """Return the filesystem path to the SQLite database file, if applicable."""
    url = DATABASE_URL
    if url.startswith("sqlite:///"):
        raw = url.replace("sqlite:///", "", 1)
        return Path(raw)
    return DEFAULT_DB_PATH


def get_database_label() -> str:
    """Human-readable database location for API responses."""
    if DATABASE_URL.startswith("sqlite:"):
        return str(get_database_path())
    # Hide credentials in postgres URLs
    if "@" in DATABASE_URL:
        scheme, rest = DATABASE_URL.split("://", 1)
        host_part = rest.split("@", 1)[-1]
        return f"{scheme}://***@{host_part}"
    return DATABASE_URL


def is_sqlite() -> bool:
    return engine.dialect.name == "sqlite"


def sql_day_of_week(column, python_weekday: int):
    """
    Filter a date column by Python weekday (0=Monday … 6=Sunday).

    Portable across SQLite and PostgreSQL.
    """
    # Both SQLite %w and PostgreSQL dow use 0=Sunday … 6=Saturday.
    sql_dow = (python_weekday + 1) % 7
    if is_sqlite():
        return func.strftime("%w", column) == str(sql_dow)
    return cast(extract("dow", column), Integer) == sql_dow


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
