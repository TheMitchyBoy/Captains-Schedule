"""Tests for prediction loading from stored schedule data."""

from datetime import date

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import ScheduleEntry
from app.predictor import ensure_prediction_patterns, predict_captain_schedule


def test_ensure_prediction_patterns_rebuilds_from_saved_entries():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)
    db = SessionLocal()

    db.add(
        ScheduleEntry(
            date_header="Thursday 6/4",
            schedule_date=date(2026, 6, 4),
            ship="Eurodam",
            checkin_time="06:30",
            return_time="10:45",
            boat_codes="BW, BWA",
            berth=None,
            ship_count=1,
            upload_batch_id="batch-a",
        )
    )
    db.add(
        ScheduleEntry(
            date_header="Thursday 6/11",
            schedule_date=date(2026, 6, 11),
            ship="Eurodam",
            checkin_time="06:30",
            return_time="10:45",
            boat_codes="BW, BWA, DrmC",
            berth=None,
            ship_count=1,
            upload_batch_id="batch-a",
        )
    )
    db.commit()

    assert ensure_prediction_patterns(db) > 0
    predictions, meta = predict_captain_schedule(db, days_ahead=30, min_confidence=0, use_ai=False)
    assert predictions
    assert meta["ai_assisted"] is False
    assert any(p.boat_code == "BW" for p in predictions)
    db.close()
