"""Tests for berth vs captain code detection."""

from app.berth_utils import is_captain_boat_code, looks_like_berth_code, split_dispatch_codes


def test_berth_codes_recognized():
    for code in ("WW", "WE", "BW", "BWA", "1", "3", "AN3", "B3T", "BERTH-WW"):
        assert looks_like_berth_code(code)
        assert not is_captain_boat_code(code)


def test_captain_codes_recognized():
    for code in ("CPT-A", "CPT-B", "OP-12", "CAP-3"):
        assert is_captain_boat_code(code)
        assert not looks_like_berth_code(code)


def test_split_dispatch_codes_filters_berths():
    codes = split_dispatch_codes("BERTH-WW, CPT-A, BW")
    assert codes == ["CPT-A"]
