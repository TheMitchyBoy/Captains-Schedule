"""
SQLAlchemy ORM models for schedule storage and learned patterns.

Tables:
  - schedule_entries: Raw rows imported from dispatch XML uploads
  - ship_capacities:  Passenger counts used to estimate port busyness
  - captain_patterns: Learned day-of-week assignment patterns per boat code
  - upload_logs:      Audit trail of each XML import
"""

from datetime import date, datetime

from sqlalchemy import Date, DateTime, Float, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class ScheduleEntry(Base):
    """
    A single dispatch row: one ship assignment on one date with check-in/out
    times and the boat/captain codes assigned to operate it.

    The unique constraint prevents duplicate rows when the same XML is uploaded
    twice or when overlapping files contain identical assignments.
    """

    __tablename__ = "schedule_entries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    date_header: Mapped[str] = mapped_column(String(255), nullable=False)  # Original dispatch line, e.g. "Thursday 6/4 - 6 ships"
    schedule_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)  # Parsed date used for queries and predictions
    ship: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    checkin_time: Mapped[str] = mapped_column(String(32), nullable=False)
    return_time: Mapped[str] = mapped_column(String(32), nullable=False)
    boat_codes: Mapped[str] = mapped_column(String(255), nullable=False, index=True)  # Tour boat / captain dispatch codes: "CPT-A / OP-12"
    berth: Mapped[str | None] = mapped_column(String(64), nullable=True)  # Port berth/dock code: WW, BW, 1, AN3 — not a tour boat
    ship_count: Mapped[int | None] = mapped_column(Integer, nullable=True)  # Parsed from date_header when available
    upload_batch_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)  # Groups rows from the same XML upload
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint(
            "schedule_date",
            "ship",
            "checkin_time",
            "return_time",
            "boat_codes",
            name="uq_schedule_entry",
        ),
    )


class ShipCapacity(Base):
    """
    Known or estimated passenger capacity for a cruise ship.

    Used by the busy-day calendar to estimate how many passengers will be in
    port on a given day based on which ships are scheduled.
    """

    __tablename__ = "ship_capacities"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ship_name: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    passenger_capacity: Mapped[int] = mapped_column(Integer, nullable=False)
    cruise_line: Mapped[str | None] = mapped_column(String(255), nullable=True)
    source: Mapped[str] = mapped_column(String(64), default="builtin")  # builtin | builtin_match | estimated | online_registry
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class CaptainPattern(Base):
    """
    A learned recurring assignment: captain X tends to work ship Y on day Z
    at specific check-in/return times.

    Rebuilt from scratch after every XML upload so patterns always reflect
    the full historical dataset. Confidence = occurrences / total shifts
    for that captain on that weekday.
    """

    __tablename__ = "captain_patterns"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    boat_code: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    day_of_week: Mapped[int] = mapped_column(Integer, nullable=False, index=True)  # 0=Monday … 6=Sunday (Python weekday convention)
    ship: Mapped[str] = mapped_column(String(255), nullable=False)
    checkin_time: Mapped[str] = mapped_column(String(32), nullable=False)
    return_time: Mapped[str] = mapped_column(String(32), nullable=False)
    occurrence_count: Mapped[int] = mapped_column(Integer, default=1)
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    last_seen: Mapped[date] = mapped_column(Date, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint(
            "boat_code",
            "day_of_week",
            "ship",
            "checkin_time",
            "return_time",
            name="uq_captain_pattern",
        ),
    )


class PredictionAdjustment(Base):
    """
    User or AI chat override for a forecasted captain shift.

    Adds appear in the predictions table; removes hide matching forecast rows.
    """

    __tablename__ = "prediction_adjustments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    action: Mapped[str] = mapped_column(String(16), nullable=False)  # add | remove
    schedule_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    boat_code: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    ship: Mapped[str] = mapped_column(String(255), nullable=False)
    checkin_time: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    return_time: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class UploadLog(Base):
    """
    Record of each XML file upload for auditing and troubleshooting.

    Stores how many rows were newly imported vs. skipped (duplicates or updates).
    """

    __tablename__ = "upload_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    batch_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    rows_imported: Mapped[int] = mapped_column(Integer, default=0)
    rows_skipped: Mapped[int] = mapped_column(Integer, default=0)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)  # Row-level parse warnings
    uploaded_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
