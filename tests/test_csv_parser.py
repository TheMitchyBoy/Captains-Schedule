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


def test_looks_like_csv_rejects_xml():
    content = b"<?xml version='1.0'?><schedules></schedules>"
    assert not looks_like_csv("file.xml", "text/xml", content)
