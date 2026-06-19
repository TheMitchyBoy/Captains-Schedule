"""Tests for berth vs captain code detection."""

from app.berth_utils import (
    is_captain_boat_code,
    looks_like_berth_code,
    repair_boat_berth_value,
    split_dispatch_codes,
)


def test_port_berth_codes_recognized():
    for code in ("WW", "WE", "1", "3", "AN3", "B3T", "BERTH-WW", "BERTH-2"):
        assert looks_like_berth_code(code)
        assert not is_captain_boat_code(code)


def test_tour_boat_codes_recognized():
    for code in ("BW", "BWA", "FNF", "50/50", "SR", "SL"):
        assert is_captain_boat_code(code)
        assert not looks_like_berth_code(code)


def test_captain_codes_recognized():
    for code in ("CPT-A", "CPT-B", "OP-12", "CAP-3"):
        assert is_captain_boat_code(code)
        assert not looks_like_berth_code(code)


def test_split_dispatch_codes_filters_berths():
    codes = split_dispatch_codes("BERTH-WW, CPT-A, BW")
    assert codes == ["CPT-A", "BW"]


def test_repair_legacy_berth_prefix_in_boat_codes():
    boat, berth = repair_boat_berth_value("BERTH-2", None)
    assert boat == ""
    assert berth == "2"


def test_repair_keeps_tour_boat_in_boat_codes():
    boat, berth = repair_boat_berth_value("BW", "2")
    assert boat == "BW"
    assert berth == "2"
