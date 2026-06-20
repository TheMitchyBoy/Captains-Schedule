"""Tests for berth vs captain code detection."""

from app.berth_utils import (
    is_captain_boat_code,
    is_placeholder_boat_code,
    looks_like_berth_code,
    repair_boat_berth_value,
    split_dispatch_codes,
)


def test_port_berth_codes_recognized():
    for code in ("WW", "WE", "1", "3", "AN3", "B3T", "BERTH-WW", "BERTH-2"):
        assert looks_like_berth_code(code)
        assert not is_captain_boat_code(code)


def test_tour_boat_codes_recognized():
    for code in ("BW", "BWA", "FNF", "50/50", "SR", "SL", "DrmC"):
        assert is_captain_boat_code(code)
        assert not looks_like_berth_code(code)


def test_cpt_placeholders_are_not_tour_boats():
    for code in ("CPT-A", "CPT-B", "cpt-c"):
        assert is_placeholder_boat_code(code)
        assert not is_captain_boat_code(code)


def test_dispatch_codes_recognized():
    for code in ("OP-12", "CAP-3"):
        assert is_captain_boat_code(code)
        assert not looks_like_berth_code(code)


def test_split_dispatch_codes_filters_berths_and_placeholders():
    codes = split_dispatch_codes("BERTH-WW, CPT-A, BW")
    assert codes == ["BW"]


def test_repair_legacy_berth_prefix_in_boat_codes():
    boat, berth = repair_boat_berth_value("BERTH-2", None)
    assert boat == ""
    assert berth == "2"


def test_repair_clears_cpt_placeholders():
    boat, berth = repair_boat_berth_value("CPT-A", "2")
    assert boat == ""
    assert berth == "2"


def test_repair_keeps_tour_boat_in_boat_codes():
    boat, berth = repair_boat_berth_value("DrmC", "2")
    assert boat == "DrmC"
    assert berth == "2"


def test_split_dispatch_codes_supports_slashes():
    assert split_dispatch_codes("DrmC / BW, BWA") == ["DrmC", "BW", "BWA"]


def test_split_dispatch_codes_supports_space_separated():
    assert split_dispatch_codes("ML BW BWA") == ["ML", "BW", "BWA"]
    assert split_dispatch_codes("DrmC 50 50 FNF GH HR") == ["DrmC", "50/50", "FNF", "GH", "HR"]


def test_merge_dispatch_codes_deduplicates():
    from app.berth_utils import merge_dispatch_codes

    merged = merge_dispatch_codes("BW, BWA", "BWA / DrmC")
    assert merged == "BW, BWA, DrmC"
