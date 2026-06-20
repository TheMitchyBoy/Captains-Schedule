"""Tests for schedule entry deduplication."""

from datetime import date

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import ScheduleEntry
from app.schedule_dedup import deduplicate_schedule_entries
from app.ship_identity import canonical_ship_key, ship_names_equivalent


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


def test_deduplicate_merges_ship_name_variants():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    db = Session()

    base = dict(
        date_header="Friday 6/19",
        schedule_date=date(2026, 6, 19),
        checkin_time="06:15",
        return_time="10:30",
        ship_count=1,
        upload_batch_id="batch-a",
        berth=None,
    )
    db.add(ScheduleEntry(**base, ship="Coral", boat_codes="AriC, BF, BS, BW"))
    db.add(ScheduleEntry(**base, ship="Coral Princess", boat_codes="AriC, BF, BS, BW"))
    db.add(ScheduleEntry(**base, ship="CORAL PRINCESS", boat_codes="AriC, BF, BS, BW"))
    db.commit()

    result = deduplicate_schedule_entries(db)
    assert result["rows_deleted"] == 2
    row = db.query(ScheduleEntry).one()
    assert row.ship == "Coral Princess"
    assert "BW" in row.boat_codes
    db.close()


def test_deduplicate_merges_port_schedule_into_tour_row():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    db = Session()

    db.add(
        ScheduleEntry(
            date_header="Friday 6/19",
            schedule_date=date(2026, 6, 19),
            ship="Coral Princess",
            checkin_time="06:15",
            return_time="10:30",
            boat_codes="AriC, BF, BS, BW",
            berth=None,
            ship_count=1,
            upload_batch_id="manual-a",
        )
    )
    db.add(
        ScheduleEntry(
            date_header="Friday 6/19",
            schedule_date=date(2026, 6, 19),
            ship="CORAL PRINCESS",
            checkin_time="06:00",
            return_time="14:00",
            boat_codes="",
            berth="2",
            ship_count=1,
            upload_batch_id="batch-b",
        )
    )
    db.commit()

    result = deduplicate_schedule_entries(db)
    assert result["rows_deleted"] == 1
    row = db.query(ScheduleEntry).one()
    assert row.checkin_time == "06:15"
    assert row.berth == "2"
    assert "BW" in row.boat_codes
    db.close()


def test_spirit_not_merged_with_carnival_spirit():
    assert not ship_names_equivalent("Spirit", "Carnival Spirit")
    assert ship_names_equivalent("C. Spirit", "Carnival Spirit")
    assert canonical_ship_key("Spirit") == "spirit"
    assert canonical_ship_key("Carnival Spirit") == "carnival spirit"


def test_deduplicate_merges_upload_port_row_with_manual_tour_row():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    db = Session()

    db.add(
        ScheduleEntry(
            date_header="Saturday 6/6",
            schedule_date=date(2026, 6, 6),
            ship="Brilliant Lady",
            checkin_time="09:00",
            return_time="13:15",
            boat_codes="DrmC 50 50 FNF GH HR",
            berth=None,
            ship_count=None,
            upload_batch_id="manual-a",
        )
    )
    db.add(
        ScheduleEntry(
            date_header="Saturday 6/6",
            schedule_date=date(2026, 6, 6),
            ship="BRILLIANT LADY",
            checkin_time="09:00",
            return_time="17:00",
            boat_codes="",
            berth=None,
            ship_count=3,
            upload_batch_id="batch-b",
        )
    )
    db.add(
        ScheduleEntry(
            date_header="Saturday 6/6",
            schedule_date=date(2026, 6, 6),
            ship="Silver Moon",
            checkin_time="08:00",
            return_time="12:15",
            boat_codes="ML BW BWA",
            berth=None,
            ship_count=None,
            upload_batch_id="manual-c",
        )
    )
    db.add(
        ScheduleEntry(
            date_header="Saturday 6/6",
            schedule_date=date(2026, 6, 6),
            ship="SILVER MOON",
            checkin_time="08:00",
            return_time="16:00",
            boat_codes="",
            berth=None,
            ship_count=2,
            upload_batch_id="batch-d",
        )
    )
    db.commit()

    result = deduplicate_schedule_entries(db)
    assert result["rows_deleted"] == 2
    assert db.query(ScheduleEntry).count() == 2

    brilliant = (
        db.query(ScheduleEntry)
        .filter(ScheduleEntry.ship.ilike("%brilliant%"))
        .one()
    )
    assert brilliant.checkin_time == "09:00"
    assert brilliant.return_time == "13:15"
    assert brilliant.ship_count == 3
    assert "DrmC" in brilliant.boat_codes
    assert "50/50" in brilliant.boat_codes

    silver = (
        db.query(ScheduleEntry)
        .filter(ScheduleEntry.ship.ilike("%silver%"))
        .one()
    )
    assert silver.return_time == "12:15"
    assert silver.ship_count == 2
    assert "ML" in silver.boat_codes
    db.close()
