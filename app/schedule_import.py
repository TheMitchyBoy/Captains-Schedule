"""
Shared schedule import persistence.

Used by both XML and CSV upload pipelines to save parsed rows with deduplication.
"""

from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from app.models import ScheduleEntry, UploadLog
from app.ship_data import get_ship_capacity


def persist_schedule_rows(
    db: Session,
    rows: list[dict],
    filename: str,
    errors: list[str],
) -> tuple[str, int, int, list[str]]:
    """
    Save parsed schedule rows to the database.

    Deduplication strategy:
      - Within the same upload batch: skip duplicate keys in-memory
      - Across uploads: update existing rows instead of inserting duplicates

    Returns (batch_id, rows_imported, rows_skipped, parse_errors).
    """
    batch_id = str(uuid.uuid4())
    imported = 0
    skipped = 0
    seen_in_batch: set[tuple] = set()

    for row in rows:
        key = (
            row["schedule_date"],
            row["ship"],
            row["checkin_time"],
            row["return_time"],
        )
        if key in seen_in_batch:
            skipped += 1
            continue
        seen_in_batch.add(key)

        existing = (
            db.query(ScheduleEntry)
            .filter(
                ScheduleEntry.schedule_date == row["schedule_date"],
                ScheduleEntry.ship == row["ship"],
                ScheduleEntry.checkin_time == row["checkin_time"],
                ScheduleEntry.return_time == row["return_time"],
            )
            .first()
        )
        if existing:
            existing.date_header = row["date_header"]
            existing.ship_count = row["ship_count"]
            existing.berth = row.get("berth")
            existing.boat_codes = row["boat_codes"][:255]
            existing.upload_batch_id = batch_id
            skipped += 1
            continue

        get_ship_capacity(db, row["ship"])

        entry = ScheduleEntry(
            date_header=row["date_header"][:255],
            schedule_date=row["schedule_date"],
            ship=row["ship"][:255],
            checkin_time=row["checkin_time"][:32],
            return_time=row["return_time"][:32],
            boat_codes=row["boat_codes"][:255],
            berth=(row.get("berth") or None),
            ship_count=row["ship_count"],
            upload_batch_id=batch_id,
        )
        db.add(entry)
        imported += 1

    if not rows and not errors:
        errors.append("No valid rows found in upload")

    notes = "; ".join(errors[:10]) if errors else None
    if errors and len(errors) > 10:
        notes += f" ... and {len(errors) - 10} more"

    log = UploadLog(
        batch_id=batch_id,
        filename=filename,
        rows_imported=imported,
        rows_skipped=skipped,
        notes=notes,
    )
    db.add(log)
    try:
        db.commit()
    except Exception as exc:
        db.rollback()
        raise ValueError(f"Database error while saving schedules: {exc}") from exc

    return batch_id, imported, skipped, errors


def clear_schedule_data(db: Session) -> int:
    """Remove all stored schedule entries (used when replacing with a fresh CSV)."""
    deleted = db.query(ScheduleEntry).delete()
    db.commit()
    return deleted
