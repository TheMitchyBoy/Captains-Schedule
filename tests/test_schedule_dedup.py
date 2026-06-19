"""Tests for schedule entry deduplication."""

from datetime import date

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import ScheduleEntry
from app.schedule_dedup import deduplicate_schedule_entries


def test_deduplicate_merges_boat_codes_and_deletes_extras():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    db = Session()

    base = dict(
        date_header="Thursday 6/4",
        schedule_date=date(2026, 6, 4),
        ship="Eurodam",
        checkin_time="06:30",
        return_time="10:45",
        ship_count=1,
        upload_batch_id="batch-a",
    )
    db.add(ScheduleEntry(**base, boat_codes="BERTH-2", berth=None))
    db.add(ScheduleEntry(**base, boat_codes="BW, BWA", berth="2"))
    db.add(ScheduleEntry(**base, boat_codes="", berth="2"))
    db.commit()

    result = deduplicate_schedule_entries(db)
    assert result["rows_deleted"] == 2
    assert result["rows_remaining"] == 1

    row = db.query(ScheduleEntry).one()
    assert row.berth == "2"
    assert "BW" in row.boat_codes
    assert "BWA" in row.boat_codes
    assert "BERTH" not in row.boat_codes.upper()
    db.close()


def test_deduplicate_leaves_distinct_time_slots():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    db = Session()

    db.add(
        ScheduleEntry(
            date_header="Thursday 6/4",
            schedule_date=date(2026, 6, 4),
            ship="Eurodam",
            checkin_time="06:30",
            return_time="10:45",
            boat_codes="BW",
            berth=None,
            ship_count=1,
            upload_batch_id="batch-a",
        )
    )
    db.add(
        ScheduleEntry(
            date_header="Thursday 6/4",
            schedule_date=date(2026, 6, 4),
            ship="Eurodam",
            checkin_time="11:00",
            return_time="15:15",
            boat_codes="DrmC",
            berth=None,
            ship_count=1,
            upload_batch_id="batch-a",
        )
    )
    db.commit()

    result = deduplicate_schedule_entries(db)
    assert result["rows_deleted"] == 0
    assert db.query(ScheduleEntry).count() == 2
    db.close()
