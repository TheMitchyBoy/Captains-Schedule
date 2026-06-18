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
        _c("CPT-A", "Symphony", "07:00", "11:00"),
        _c("CPT-A", "Carnival", "08:00", "12:00"),
        _c("CPT-B", "Carnival", "08:00", "12:00"),
    ]
    scheduled = _schedule_day(candidates)
    cpt_a = [s for s in scheduled if s.boat_code == "CPT-A"]
    assert len(cpt_a) == 1
    assert len(scheduled) == 2


def test_three_hour_turnaround():
    """Same boat needs 3 hours between consecutive tours."""
    candidates = [
        _c("CPT-A", "Symphony", "07:00", "10:00"),
        _c("CPT-A", "Carnival", "12:00", "15:00"),  # only 2h gap — reject
        _c("CPT-A", "Norwegian", "13:30", "16:30"),  # 3.5h gap — accept
    ]
    scheduled = _schedule_day(candidates)
    ships = {s.ship for s in scheduled if s.boat_code == "CPT-A"}
    assert "Symphony" in ships
    assert "Norwegian" in ships
    assert "Carnival" not in ships


def test_alphabetical_boat_priority():
    """When multiple boats are eligible, alphabetical order is preferred."""
    candidates = [
        _c("CPT-B", "Symphony", "07:00", "11:00", conf=0.95),
        _c("CPT-A", "Symphony", "07:00", "11:00", conf=0.5),
    ]
    scheduled = _schedule_day(candidates)
    assert len(scheduled) == 1
    assert scheduled[0].boat_code == "CPT-A"


def test_one_boat_per_ship_per_window():
    """Each ship/time slot gets at most one boat assignment."""
    candidates = [
        _c("CPT-A", "Symphony", "07:00", "11:00"),
        _c("CPT-B", "Symphony", "07:00", "11:00"),
    ]
    scheduled = _schedule_day(candidates)
    assert len(scheduled) == 1


def test_apply_constraints_across_days():
    candidates = [
        _c("CPT-A", "Symphony", "07:00", "11:00", day=date(2026, 6, 4)),
        _c("CPT-A", "Symphony", "07:00", "11:00", day=date(2026, 6, 5)),
    ]
    result = apply_scheduling_constraints(candidates)
    assert len(result) == 2
