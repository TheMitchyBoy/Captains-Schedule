from datetime import date, datetime

from sqlalchemy import Date, DateTime, Float, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class ScheduleEntry(Base):
    __tablename__ = "schedule_entries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    date_header: Mapped[str] = mapped_column(String(255), nullable=False)
    schedule_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    ship: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    checkin_time: Mapped[str] = mapped_column(String(32), nullable=False)
    return_time: Mapped[str] = mapped_column(String(32), nullable=False)
    boat_codes: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    ship_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    upload_batch_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
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
    __tablename__ = "ship_capacities"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ship_name: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    passenger_capacity: Mapped[int] = mapped_column(Integer, nullable=False)
    cruise_line: Mapped[str | None] = mapped_column(String(255), nullable=True)
    source: Mapped[str] = mapped_column(String(64), default="builtin")
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class CaptainPattern(Base):
    """Learned patterns for captain assignments."""

    __tablename__ = "captain_patterns"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    boat_code: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    day_of_week: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
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


class UploadLog(Base):
    __tablename__ = "upload_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    batch_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    rows_imported: Mapped[int] = mapped_column(Integer, default=0)
    rows_skipped: Mapped[int] = mapped_column(Integer, default=0)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    uploaded_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
