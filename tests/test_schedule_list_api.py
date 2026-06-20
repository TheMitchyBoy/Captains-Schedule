"""Tests for schedule list filters."""

from datetime import date, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import ScheduleEntry


def test_through_today_filter_excludes_future_rows():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    db = Session()

    today = date.today()
    db.add(
        ScheduleEntry(
            date_header="Today",
            schedule_date=today,
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
            date_header="Future",
            schedule_date=today + timedelta(days=30),
            ship="Koningsdam",
            checkin_time="11:00",
            return_time="15:15",
            boat_codes="DrmC",
            berth=None,
            ship_count=1,
            upload_batch_id="batch-a",
        )
    )
    db.commit()

    all_rows = db.query(ScheduleEntry).all()
    assert len(all_rows) == 2

    historical = (
        db.query(ScheduleEntry)
        .filter(ScheduleEntry.schedule_date <= today)
        .order_by(ScheduleEntry.schedule_date.desc())
        .all()
    )
    assert len(historical) == 1
    assert historical[0].ship == "Eurodam"
    db.close()
