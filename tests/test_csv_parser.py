"""Tests for CSV schedule parsing."""

from pathlib import Path

from app.csv_parser import looks_like_csv, parse_csv_content


def test_parse_ketchikan_sample_csv():
    content = Path("sample_data/ketchikan_schedule.csv").read_bytes()
    rows, errors = parse_csv_content(content, "ketchikan_schedule.csv")
    assert len(rows) == 46
    assert not errors
    assert rows[0]["ship"] == "Brilliant Lady"
    assert rows[0]["checkin_time"] == "09:00"
    assert rows[0]["berth"] == "3"
    assert rows[0]["boat_codes"] == "CPT-A"


def test_looks_like_csv():
    content = Path("sample_data/ketchikan_schedule.csv").read_bytes()
    assert looks_like_csv("schedule.csv", "text/csv", content)


def test_parse_csv_with_tour_boat_column():
    csv_text = (
        "date,ship,arrival,departure,berth,boat\n"
        "2026-05-01,NOORDAM,7:00 AM,1:00 PM,2,BW\n"
        "2026-05-03,SAFARI ENDEAVOUR,5:30 AM,1:00 PM,4,BWA\n"
    )
    rows, errors = parse_csv_content(csv_text.encode(), "dispatch.csv")
    assert not errors
    assert len(rows) == 2
    assert rows[0]["berth"] == "2"
    assert rows[0]["boat_codes"] == "BW"
    assert rows[1]["berth"] == "4"
    assert rows[1]["boat_codes"] == "BWA"


def test_parse_csv_repair_legacy_berth_in_boat_field():
    csv_text = (
        "date,ship,arrival,departure,boat\n"
        "2026-05-01,NOORDAM,7:00 AM,1:00 PM,BERTH-2\n"
    )
    rows, errors = parse_csv_content(csv_text.encode(), "legacy.csv")
    assert not errors
    assert rows[0]["berth"] == "2"
    assert rows[0]["boat_codes"] == "CPT-A"


def test_looks_like_csv_rejects_xml():
    content = b"<?xml version='1.0'?><schedules></schedules>"
    assert not looks_like_csv("file.xml", "text/xml", content)
