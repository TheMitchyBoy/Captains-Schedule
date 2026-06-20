"""Tests for canonical ship identity resolution."""

from app.ship_identity import best_ship_display_name, canonical_ship_key, ship_names_equivalent


def test_coral_variants_equivalent():
    assert ship_names_equivalent("Coral", "Coral Princess")
    assert ship_names_equivalent("CORAL PRINCESS", "Coral Princess")
    assert canonical_ship_key("Coral") == "coral princess"


def test_c_spirit_resolves_to_carnival_spirit():
    assert ship_names_equivalent("C. Spirit", "Carnival Spirit")
    assert canonical_ship_key("C. Spirit") == "carnival spirit"


def test_bare_spirit_stays_ambiguous():
    assert canonical_ship_key("Spirit") == "spirit"
    assert not ship_names_equivalent("Spirit", "Carnival Spirit")


def test_best_ship_display_name_prefers_full_name():
    assert best_ship_display_name(["Coral", "CORAL PRINCESS", "Coral Princess"]) == "Coral Princess"
