"""Tests for prediction adjustments and AI chat wiring."""

from datetime import date

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.ai_prediction_chat import chat_with_prediction_assistant
from app.database import Base
from app.models import ScheduleEntry
from app.prediction_adjustments import (
    apply_prediction_adjustments,
    create_prediction_adjustment,
    list_prediction_adjustments,
)
from app.predictor import ensure_prediction_patterns, predict_captain_schedule
from app.schemas import CaptainPrediction


def _make_db():
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
    db.commit()
    ensure_prediction_patterns(db)
    return db


def test_apply_add_and_remove_adjustments():
    db = _make_db()
    predictions, _ = predict_captain_schedule(db, days_ahead=30, min_confidence=0, use_ai=False)
    assert predictions

    target = predictions[0]
    create_prediction_adjustment(
        db,
        action="remove",
        schedule_date=target.schedule_date,
        boat_code=target.boat_code,
        ship=target.ship,
    )
    create_prediction_adjustment(
        db,
        action="add",
        schedule_date=target.schedule_date,
        boat_code="DrmC",
        ship=target.ship,
        checkin_time=target.checkin_time,
        return_time=target.return_time,
    )

    updated, _ = predict_captain_schedule(db, days_ahead=30, min_confidence=0, use_ai=False)
    assert not any(
        p.schedule_date == target.schedule_date
        and p.boat_code == target.boat_code
        and p.ship == target.ship
        for p in updated
    )
    assert any(
        p.schedule_date == target.schedule_date and p.boat_code == "DrmC" for p in updated
    )
    db.close()


def test_apply_prediction_adjustments_direct():
    db = _make_db()
    create_prediction_adjustment(
        db,
        action="add",
        schedule_date=date(2026, 6, 20),
        boat_code="DrmC",
        ship="Eurodam",
        checkin_time="06:30",
        return_time="10:45",
    )
    adjustments = list_prediction_adjustments(db)
    base = [
        CaptainPrediction(
            boat_code="BW",
            schedule_date=date(2026, 6, 20),
            day_of_week="Saturday",
            ship="Eurodam",
            checkin_time="06:30",
            return_time="10:45",
            confidence=0.8,
            busy_score=0.4,
            source="pattern",
        )
    ]
    merged = apply_prediction_adjustments(base, adjustments)
    assert len(merged) == 2
    assert any(row.boat_code == "DrmC" for row in merged)
    db.close()


def test_chat_without_openai_returns_helpful_message(monkeypatch):
    db = _make_db()
    monkeypatch.setattr("app.ai_prediction_chat.get_settings", lambda: type("S", (), {
        "openai": type("O", (), {"is_configured": False})()
    })())
    result = chat_with_prediction_assistant(db, message="Add BW to Eurodam tomorrow")
    assert "OpenAI is not configured" in result["reply"]
    assert result["actions_applied"] == []
    db.close()


def test_list_prediction_adjustments():
    db = _make_db()
    create_prediction_adjustment(
        db,
        action="add",
        schedule_date=date(2026, 6, 20),
        boat_code="JR",
        ship="Carnival Spirit",
        checkin_time="07:00",
        return_time="11:30",
    )
    rows = list_prediction_adjustments(db)
    assert len(rows) == 1
    assert rows[0].boat_code == "JR"
    db.close()
