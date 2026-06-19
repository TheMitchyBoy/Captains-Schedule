"""Tests for MMS dispatch message tour extraction."""

from datetime import date
from pathlib import Path

from app.mms_dispatch_parser import (
    extract_mms_dispatch_rows,
    parse_tour_lines_from_text,
    resolve_dispatch_ship_name,
)
from app.xml_parser import parse_xml_content


SAMPLE = Path("sample_data/mms_dispatch_sample.xml").read_bytes()


def test_parse_multiple_tours_in_one_message_body():
    rows, errors = parse_xml_content(SAMPLE, "mms_dispatch_sample.xml")
    assert not errors or all("Extracted" in e or "tour dispatch" in e for e in errors)
    assert len(rows) >= 7

    ships = {row["ship"] for row in rows}
    assert "Eurodam" in ships
    assert "Koningsdam" in ships
    assert "Island Princess" in ships
    assert "Celebrity Solstice" in ships or "Solstice" in ships

    eurodam = [r for r in rows if r["ship"] == "Eurodam" and r["schedule_date"] == date(2026, 6, 4)]
    assert eurodam
    assert "BW" in eurodam[0]["boat_codes"]
    assert eurodam[0]["checkin_time"] == "06:30"
    assert eurodam[0]["return_time"] == "10:45"


def test_extracts_june_18_dispatches():
    rows, _ = parse_xml_content(SAMPLE, "mms_dispatch_sample.xml")
    june_18 = [r for r in rows if r["schedule_date"] == date(2026, 6, 18)]
    assert len(june_18) >= 2
    koningsdam = [r for r in june_18 if r["ship"] == "Koningsdam"]
    assert koningsdam
    assert "LewE" in koningsdam[0]["boat_codes"] or "JR" in koningsdam[0]["boat_codes"]


def test_parse_tour_lines_from_plain_text():
    text = """
Monday 6/8
Queen Elizabeth — BW, BWA, DrmC, 50/50 — 08:00–12:00
6/11 Royal — BW, BWA, DrmC, 50/50 — 07:15–11:30
"""
    rows, errors = parse_tour_lines_from_text(text, 2026)
    assert not errors
    assert len(rows) == 2
    assert rows[0]["ship"] == "Queen Elizabeth"
    assert rows[0]["checkin_time"] == "08:00"


def test_resolve_island_to_island_princess():
    assert resolve_dispatch_ship_name("Island") == "Island Princess"


def test_resolve_c_spirit_parenthetical():
    assert resolve_dispatch_ship_name("C. Spirit (Carnival Spirit)") == "Carnival Spirit"
    assert resolve_dispatch_ship_name("C. Spirit") == "Carnival Spirit"


def test_carnival_spirit_may_3_jr_lewe_7am():
    text = """
Sunday 5/3
C. Spirit (Carnival Spirit) — JR, LewE — 7am-11:30am
"""
    rows, errors = parse_tour_lines_from_text(text, 2026)
    assert not errors
    assert len(rows) == 1
    assert rows[0]["schedule_date"] == date(2026, 5, 3)
    assert rows[0]["ship"] == "Carnival Spirit"
    assert rows[0]["checkin_time"] == "07:00"
    assert rows[0]["return_time"] == "11:30"
    assert "JR" in rows[0]["boat_codes"]
    assert "LewE" in rows[0]["boat_codes"]


def test_carnival_spirit_at_7am_format():
    text = "5/3 C. Spirit (Carnival Spirit) — JR, LewE at 7am-11:30am"
    rows, errors = parse_tour_lines_from_text(text, 2026)
    assert not errors
    assert len(rows) == 1
    assert rows[0]["ship"] == "Carnival Spirit"
    assert rows[0]["checkin_time"] == "07:00"


def test_clean_xml_preserves_mms_tour_rows():
    from app.xml_cleaner import clean_xml_content

    result = clean_xml_content(SAMPLE)
    assert len(result.entries) >= 7
    assert any("BW" in entry.get("boat_codes", "") for entry in result.entries)


def test_extract_mms_dispatch_rows_finds_bw_tours():
    rows, _ = extract_mms_dispatch_rows(SAMPLE)
    bw_rows = [r for r in rows if "BW" in r["boat_codes"]]
    assert len(bw_rows) >= 6
