"""Scheduler trigger collection over a simulated clock, against a real temp DB.

Drives the real `_check_one_time_schedules` / `_check_recurring_schedules`
tick by tick (10 s, like production) with `now_local`/`now_utc` frozen to a
fake clock, and asserts which announcements land in the queue and when.
Storage-time conversion (naive Istanbul → UTC) is NOT patched.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

import database as db
import scheduler as scheduler_mod
from scheduler import Scheduler

IST = ZoneInfo("Europe/Istanbul")
MONDAY = datetime(2026, 10, 5, tzinfo=IST)    # weekday 0
SATURDAY = datetime(2026, 10, 10, tzinfo=IST)  # weekday 5


class FakeClock:
    def __init__(self, start: datetime):
        self.now = start

    def local(self):
        return self.now

    def utc(self):
        return self.now.astimezone(timezone.utc)


@pytest.fixture
def clock(monkeypatch):
    c = FakeClock(MONDAY)
    monkeypatch.setattr(scheduler_mod, "now_local", c.local)
    monkeypatch.setattr(scheduler_mod, "now_utc", c.utc)
    return c


@pytest.fixture
def announcement_id(temp_db, tmp_path):
    path = tmp_path / "anons.mp3"
    path.write_bytes(b"")
    return db.add_media_file("anons.mp3", str(path), "announcement", 12)


def _run(sched: Scheduler, clock: FakeClock, start: datetime, end: datetime,
         step_seconds: int = 10, outside_working_hours: bool = False, skip=None):
    """Tick from start to end; return [(local HH:MM at queue time, source, schedule_id)]."""
    seen = []
    clock.now = start
    while clock.now <= end:
        if not (skip and skip[0] <= clock.now < skip[1]):
            before = len(sched._announcement_queue)
            sched._check_one_time_schedules(
                outside_working_hours=outside_working_hours, silence_blocked=False
            )
            sched._check_recurring_schedules(
                outside_working_hours=outside_working_hours, silence_blocked=False
            )
            for item in list(sched._announcement_queue)[before:]:
                seen.append((clock.now.strftime("%H:%M"), item["source"], item["schedule_id"]))
        clock.now += timedelta(seconds=step_seconds)
    return seen


def test_recurring_specific_times_fire_once_per_minute_on_listed_days(clock, announcement_id):
    sid = db.add_recurring_schedule(announcement_id, [0, 1, 2, 3, 4], "09:00", None, 0,
                                    ["09:00", "13:00"], None)
    sched = Scheduler()

    seen = _run(sched, clock, MONDAY.replace(hour=8, minute=58), MONDAY.replace(hour=13, minute=2))
    assert seen == [("09:00", "recurring", sid), ("13:00", "recurring", sid)]

    sched = Scheduler()
    seen = _run(sched, clock, SATURDAY.replace(hour=8, minute=58), SATURDAY.replace(hour=9, minute=2))
    assert seen == []


def test_recurring_interval_fires_on_grid_points(clock, announcement_id):
    """Interval schedules fire on start + k*interval. They used to re-fire one
    tick early relative to the previous actual fire, drifting ~10 s earlier
    every time (a 30-min schedule ran ~4 min early by evening)."""
    sid = db.add_recurring_schedule(announcement_id, [5, 6], "10:00", "18:00", 30, None, None)
    sched = Scheduler()

    seen = _run(sched, clock, SATURDAY.replace(hour=9, minute=55), SATURDAY.replace(hour=11, minute=5))
    assert seen == [
        ("10:00", "recurring", sid),
        ("10:30", "recurring", sid),
        ("11:00", "recurring", sid),
    ]


def test_recurring_interval_full_day_has_no_drift(clock, announcement_id):
    sid = db.add_recurring_schedule(announcement_id, [5], "09:00", "21:00", 5, None, None)
    sched = Scheduler()

    seen = _run(sched, clock, SATURDAY.replace(hour=8, minute=59), SATURDAY.replace(hour=21, minute=1))
    times = [t for t, _, s in seen if s == sid]
    assert len(times) == 145                       # 09:00, 09:05, ..., 21:00
    assert times[0] == "09:00" and times[-1] == "21:00"
    assert all(int(t[3:]) % 5 == 0 for t in times)


def test_recurring_interval_overnight_window(clock, announcement_id):
    sid = db.add_recurring_schedule(announcement_id, [5, 6], "22:00", "06:00", 60, None, None)
    sched = Scheduler()

    seen = _run(sched, clock, SATURDAY.replace(hour=21, minute=55),
                SATURDAY.replace(hour=6, minute=5) + timedelta(days=1))
    assert [t for t, _, s in seen if s == sid] == [
        "22:00", "23:00", "00:00", "01:00", "02:00", "03:00", "04:00", "05:00", "06:00",
    ]


def test_one_time_naive_local_schedule_fires_once_at_local_time(clock, announcement_id):
    sid = db.add_one_time_schedule(announcement_id, datetime(2026, 10, 5, 10, 30), "kampanya")
    sched = Scheduler()

    seen = _run(sched, clock, MONDAY.replace(hour=10, minute=25), MONDAY.replace(hour=10, minute=40))
    assert seen == [("10:30", "one_time", sid)]


def test_announcements_are_not_queued_outside_working_hours(clock, announcement_id):
    db.add_recurring_schedule(announcement_id, [0], "09:00", None, 0, ["09:00"], None)
    sched = Scheduler()

    seen = _run(sched, clock, MONDAY.replace(hour=8, minute=59), MONDAY.replace(hour=9, minute=1),
                outside_working_hours=True)
    assert seen == []


def test_stalled_tick_skips_recurring_minute_but_not_one_time(clock, announcement_id):
    """Current behaviour (backlog BL-RECURRING-MINUTE-MISS), not a desired one.

    Recurring specific-time triggers need a tick *inside* the exact HH:MM
    minute; there is no catch-up. One-time schedules tolerate 120 s.
    Simulates the scheduler thread blocked from 12:59:55 to 13:01:05.
    """
    rec = db.add_recurring_schedule(announcement_id, [0], "13:00", None, 0, ["13:00"], None)
    one = db.add_one_time_schedule(announcement_id, datetime(2026, 10, 5, 13, 0), None)
    sched = Scheduler()

    stall = (MONDAY.replace(hour=12, minute=59, second=55), MONDAY.replace(hour=13, minute=1, second=5))
    seen = _run(sched, clock, MONDAY.replace(hour=12, minute=58), MONDAY.replace(hour=13, minute=3),
                skip=stall)

    assert ("13:01", "one_time", one) in seen
    assert not any(src == "recurring" and s == rec for _, src, s in seen)
