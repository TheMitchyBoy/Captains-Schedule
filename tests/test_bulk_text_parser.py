"""Tests for relaxed bulk tour paste parsing."""

from datetime import date

from app.bulk_text_parser import (
    normalize_bulk_paste,
    parse_relaxed_bulk_lines,
    rows_from_prose_fallback,
    rows_from_tabular_fallback,
)


def test_multiline_ship_boats_times_block():
    text = """
Coral Princess
AriC, BF, BS, BW
6:15-10:30
Eurodam
BW, BWA
06:30-10:45
"""
    rows, errors = parse_relaxed_bulk_lines(text, date(2026, 6, 19))
    assert not errors
    assert len(rows) == 2
    assert rows[0]["ship"] == "Coral Princess"
    assert rows[0]["checkin_time"] == "06:15"
    assert "BW" in rows[0]["boat_codes"]


def test_tab_separated_line():
    text = "Coral Princess\tAriC, BF, BS, BW\t6:15\t10:30"
    rows, _ = parse_relaxed_bulk_lines(text, date(2026, 6, 19))
    assert len(rows) == 1
    assert rows[0]["return_time"] == "10:30"


def test_compact_times_expanded():
    normalized = normalize_bulk_paste("Coral — AriC, BF — 615-1030")
    assert "6:15" in normalized
    assert "10:30" in normalized


def test_prose_until_line():
    text = "Spirit had JR and LewE at 7am until 1130"
    rows, errors = rows_from_prose_fallback(text, date(2026, 5, 3))
    assert not errors
    assert len(rows) == 1
    assert rows[0]["ship"] == "Carnival Spirit"
    assert rows[0]["checkin_time"] == "07:00"
    assert rows[0]["return_time"] == "11:30"
    assert "JR" in rows[0]["boat_codes"]


def test_tabular_fallback_csv_style():
    text = "Coral Princess,AriC BF BS BW,6:15,10:30"
    rows, errors = rows_from_tabular_fallback(text, date(2026, 6, 19))
    assert not errors
    assert len(rows) == 1
    assert rows[0]["ship"] == "Coral Princess"
