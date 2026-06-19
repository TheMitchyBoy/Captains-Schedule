"""Tests for legacy berth/boat field repair."""

from datetime import date

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import ScheduleEntry
from app.schedule_repair import repair_schedule_berth_mixups


def test_repair_moves_berth_prefix_out_of_boat_codes():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    db = Session()

    entry = ScheduleEntry(
        date_header="Friday 5/1",
        schedule_date=date(2026, 5, 1),
        ship="NOORDAM",
        checkin_time="07:00",
        return_time="13:00",
        boat_codes="BERTH-2",
        berth=None,
        ship_count=1,
        upload_batch_id="test-batch",
    )
    db.add(entry)
    db.commit()

    updated = repair_schedule_berth_mixups(db)
    db.refresh(entry)

    assert updated >= 1
    assert entry.berth == "2"
    assert entry.boat_codes == ""
    db.close()
