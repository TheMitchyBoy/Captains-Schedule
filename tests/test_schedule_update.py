"""Tests for manual schedule row create and update."""

from datetime import date

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import ScheduleEntry
from app.schedule_update import bulk_create_schedule_entries, create_schedule_entry, update_schedule_entry


def _make_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    return Session()


def _add_entry(db, **overrides):
    data = dict(
        date_header="Sunday 5/3",
        schedule_date=date(2026, 5, 3),
        ship="Carnival Spirit",
        checkin_time="07:00",
        return_time="11:30",
        boat_codes="",
        berth=None,
        ship_count=1,
        upload_batch_id="batch-a",
    )
    data.update(overrides)
    entry = ScheduleEntry(**data)
    db.add(entry)
    db.commit()
    db.refresh(entry)
    return entry


def test_update_boat_codes_and_times():
    db = _make_session()
    entry = _add_entry(db)

    updated = update_schedule_entry(
        db,
        entry.id,
        checkin_time="7am",
        return_time="11:30am",
        boat_codes="JR, LewE",
    )

    assert updated.checkin_time == "07:00"
    assert updated.return_time == "11:30"
    assert updated.boat_codes == "JR, LewE"
    db.close()


def test_update_rejects_duplicate_row():
    db = _make_session()
    first = _add_entry(db, boat_codes="JR, LewE")
    second = _add_entry(
        db,
        checkin_time="11:00",
        return_time="15:15",
        boat_codes="BW",
    )

    try:
        update_schedule_entry(
            db,
            second.id,
            checkin_time="07:00",
            return_time="11:30",
            boat_codes="JR, LewE",
        )
        assert False, "expected ValueError"
    except ValueError as exc:
        assert "already exists" in str(exc).lower()

    unchanged = db.query(ScheduleEntry).filter(ScheduleEntry.id == second.id).one()
    assert unchanged.checkin_time == "11:00"
    assert unchanged.boat_codes == "BW"
    assert first.boat_codes == "JR, LewE"
    db.close()


def test_update_requires_at_least_one_field():
    db = _make_session()
    entry = _add_entry(db, boat_codes="BW")

    try:
        update_schedule_entry(db, entry.id)
        assert False, "expected ValueError"
    except ValueError as exc:
        assert "at least one field" in str(exc).lower()
    db.close()


def test_create_schedule_entry():
    db = _make_session()

    entry = create_schedule_entry(
        db,
        schedule_date=date(2026, 5, 3),
        ship="Carnival Spirit",
        checkin_time="7am",
        return_time="11:30am",
        boat_codes="JR, LewE",
        berth="2",
    )

    assert entry.ship == "Carnival Spirit"
    assert entry.checkin_time == "07:00"
    assert entry.return_time == "11:30"
    assert entry.boat_codes == "JR, LewE"
    assert entry.berth == "2"
    assert entry.date_header == "Sunday 5/3"
    assert entry.upload_batch_id.startswith("manual-")
    db.close()


def test_create_merges_boats_into_existing_slot():
    db = _make_session()
    _add_entry(db, boat_codes="BW")

    entry = create_schedule_entry(
        db,
        schedule_date=date(2026, 5, 3),
        ship="Carnival Spirit",
        checkin_time="07:00",
        return_time="11:30",
        boat_codes="JR, LewE",
    )

    assert db.query(ScheduleEntry).count() == 1
    assert "BW" in entry.boat_codes
    assert "JR" in entry.boat_codes
    db.close()


def test_create_rejects_exact_duplicate():
    db = _make_session()
    _add_entry(db, boat_codes="JR, LewE")

    try:
        create_schedule_entry(
            db,
            schedule_date=date(2026, 5, 3),
            ship="Carnival Spirit",
            checkin_time="07:00",
            return_time="11:30",
            boat_codes="JR, LewE",
        )
        assert False, "expected ValueError"
    except ValueError as exc:
        assert "already exists" in str(exc).lower()
    db.close()


def test_bulk_create_schedule_entries():
    db = _make_session()
    text = """
C. Spirit (Carnival Spirit) — JR, LewE — 7am-11:30am
Eurodam — BW, BWA — 06:30–10:45
"""
    result = bulk_create_schedule_entries(db, text, date(2026, 5, 3))

    assert result.rows_parsed == 2
    assert result.rows_created == 2
    assert result.rows_merged == 0
    assert result.rows_skipped == 0
    assert not result.errors
    assert db.query(ScheduleEntry).count() == 2
    for row in db.query(ScheduleEntry).all():
        assert row.schedule_date == date(2026, 5, 3)
    db.close()


def test_bulk_create_ignores_inline_dates_when_day_selected():
    db = _make_session()
    text = "6/4 Eurodam — BW — 06:30–10:45"
    result = bulk_create_schedule_entries(db, text, date(2026, 5, 3))

    assert result.rows_parsed == 1
    row = db.query(ScheduleEntry).one()
    assert row.schedule_date == date(2026, 5, 3)
    db.close()


def test_bulk_create_merges_existing_slot():
    db = _make_session()
    _add_entry(db, boat_codes="BW")

    text = "5/3 Carnival Spirit — JR, LewE — 07:00–11:30"
    result = bulk_create_schedule_entries(db, text, date(2026, 5, 3))

    assert result.rows_parsed == 1
    assert result.rows_merged == 1
    assert db.query(ScheduleEntry).count() == 1
    row = db.query(ScheduleEntry).one()
    assert "BW" in row.boat_codes
    assert "JR" in row.boat_codes
    db.close()


def test_bulk_create_skips_exact_duplicate():
    db = _make_session()
    _add_entry(db, boat_codes="JR, LewE")

    text = "5/3 Carnival Spirit — JR, LewE — 07:00–11:30"
    result = bulk_create_schedule_entries(db, text, date(2026, 5, 3))

    assert result.rows_parsed == 1
    assert result.rows_skipped == 1
    assert db.query(ScheduleEntry).count() == 1
    db.close()


def test_bulk_create_parses_prose_without_ai():
    db = _make_session()
    messy = "Spirit had JR and LewE at 7am until 1130"

    result = bulk_create_schedule_entries(db, messy, date(2026, 5, 3), use_ai=False)

    assert result.ai_assisted is False
    assert result.rows_parsed == 1
    assert result.rows_created == 1
    row = db.query(ScheduleEntry).one()
    assert row.ship == "Carnival Spirit"
    assert "JR" in row.boat_codes
    db.close()


def test_bulk_create_uses_ai_when_parser_finds_nothing(monkeypatch):
    db = _make_session()
    messy = "notes: assign BW and BWA to the morning Eurodam group starting 630 ending 1045"

    def fake_ai_parse(text, schedule_date, db=None):
        assert text == messy
        assert schedule_date == date(2026, 5, 3)
        return [
            {
                "date_header": "Sunday 5/3",
                "schedule_date": date(2026, 5, 3),
                "ship": "Eurodam",
                "checkin_time": "06:30",
                "return_time": "10:45",
                "boat_codes": "BW, BWA",
                "ship_count": None,
            }
        ], "AI decoded 1 tour(s) from pasted text"

    monkeypatch.setattr("app.ai_tour_parser.ai_parse_tour_lines", fake_ai_parse)

    result = bulk_create_schedule_entries(db, messy, date(2026, 5, 3), use_ai=True)

    assert result.ai_assisted is True
    assert result.rows_parsed == 1
    assert result.rows_created == 1
    row = db.query(ScheduleEntry).one()
    assert row.ship == "Eurodam"
    assert "BW" in row.boat_codes
    db.close()


def test_bulk_create_skips_ai_when_disabled(monkeypatch):
    db = _make_session()

    def fake_ai_parse(text, schedule_date, db=None):
        raise AssertionError("AI should not be called when use_ai=False")

    monkeypatch.setattr("app.ai_tour_parser.ai_parse_tour_lines", fake_ai_parse)

    result = bulk_create_schedule_entries(db, "Spirit JR 7am", date(2026, 5, 3), use_ai=False)

    assert result.rows_parsed == 0
    assert result.ai_assisted is False
    assert any("no tour lines" in err.lower() for err in result.errors)
    db.close()


