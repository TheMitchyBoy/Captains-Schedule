"""Tests for scheduling constraint logic."""

from datetime import date

from app.scheduler import ShiftCandidate, _schedule_day, apply_scheduling_constraints


def _c(boat, ship, checkin, ret, conf=0.9, day=None):
    return ShiftCandidate(
        boat_code=boat,
        schedule_date=day or date(2026, 6, 4),
        day_of_week="Thursday",
        ship=ship,
        checkin_time=checkin,
        return_time=ret,
        confidence=conf,
        passenger_estimate=3000,
        busy_score=0.5,
    )


def test_no_overlapping_boat_assignments():
    """A boat cannot serve two ships at the same time."""
    candidates = [
        _c("BW", "Symphony", "07:00", "11:00"),
        _c("BW", "Carnival", "08:00", "12:00"),
        _c("BWA", "Carnival", "08:00", "12:00"),
    ]
    scheduled = _schedule_day(candidates)
    bw = [s for s in scheduled if s.boat_code == "BW"]
    assert len(bw) == 1
    assert len(scheduled) == 2


def test_three_hour_turnaround():
    """Same boat needs 3 hours between consecutive tours."""
    candidates = [
        _c("BW", "Symphony", "07:00", "10:00"),
        _c("BW", "Carnival", "12:00", "15:00"),  # only 2h gap — reject
        _c("BW", "Norwegian", "13:30", "16:30"),  # 3.5h gap — accept
    ]
    scheduled = _schedule_day(candidates)
    ships = {s.ship for s in scheduled if s.boat_code == "BW"}
    assert "Symphony" in ships
    assert "Norwegian" in ships
    assert "Carnival" not in ships


def test_alphabetical_boat_priority():
    """When multiple boats are eligible, alphabetical order is preferred."""
    candidates = [
        _c("BWA", "Symphony", "07:00", "11:00", conf=0.95),
        _c("BW", "Symphony", "07:00", "11:00", conf=0.5),
    ]
    scheduled = _schedule_day(candidates)
    assert len(scheduled) == 1
    assert scheduled[0].boat_code == "BW"


def test_multiple_boats_per_ship_when_expected():
    """Each ship/time slot may receive multiple boats when history shows it needs them."""
    candidates = [
        _c("BW", "Symphony", "07:00", "11:00"),
        _c("BWA", "Symphony", "07:00", "11:00"),
        _c("DrmC", "Symphony", "07:00", "11:00"),
    ]
    slot_counts = {("Symphony", 3, "07:00", "11:00"): 2}
    scheduled = _schedule_day(candidates, slot_boat_counts=slot_counts, day_of_week=3)
    assert len(scheduled) == 2
    assert {s.boat_code for s in scheduled} == {"BW", "BWA"}


def test_apply_constraints_across_days():
    candidates = [
        _c("BW", "Symphony", "07:00", "11:00", day=date(2026, 6, 4)),
        _c("BW", "Symphony", "07:00", "11:00", day=date(2026, 6, 5)),
    ]
    result = apply_scheduling_constraints(candidates)
    assert len(result) == 2
