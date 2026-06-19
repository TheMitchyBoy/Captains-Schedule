"""Tests for per-ship boat count learning."""

from datetime import date

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import ScheduleEntry
from app.predictor import learn_expected_boat_counts


def test_learn_expected_boat_counts_uses_max_observed():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    db = Session()

    db.add(
        ScheduleEntry(
            date_header="Thursday 6/4",
            schedule_date=date(2026, 6, 4),
            ship="Norwegian Bliss",
            checkin_time="07:00",
            return_time="15:00",
            boat_codes="BW, BWA",
            berth="2",
            ship_count=1,
            upload_batch_id="batch-1",
        )
    )
    db.add(
        ScheduleEntry(
            date_header="Thursday 6/11",
            schedule_date=date(2026, 6, 11),
            ship="Norwegian Bliss",
            checkin_time="07:00",
            return_time="15:00",
            boat_codes="BW, BWA, DrmC",
            berth="2",
            ship_count=1,
            upload_batch_id="batch-1",
        )
    )
    db.add(
        ScheduleEntry(
            date_header="Thursday 6/4",
            schedule_date=date(2026, 6, 4),
            ship="Safari Quest",
            checkin_time="07:00",
            return_time="13:00",
            boat_codes="50/50",
            berth="4",
            ship_count=1,
            upload_batch_id="batch-1",
        )
    )
    db.commit()

    counts = learn_expected_boat_counts(db)
    assert counts[("Norwegian Bliss", 3, "07:00", "15:00")] == 3
    assert counts[("Safari Quest", 3, "07:00", "13:00")] == 1
    db.close()
