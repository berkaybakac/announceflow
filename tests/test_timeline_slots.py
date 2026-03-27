"""
Timeline slot service tests — slot map logic used by the Haftalık/Günlük
Zaman Çizelgesi UI.

Categories:
  happy   — normal usage, expected happy path
  error   — invalid/missing inputs that must not crash
  edge    — date boundaries (leap year, year-end, week rollover)
  security — malicious / adversarial date strings
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services.slot_map_service import (
    RawSlot,
    _minutes_to_hhmm,
    _split_at_midnight,
    get_day_slots,
    get_week_slots,
)


# ── fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _db_empty(monkeypatch):
    """Stub DB so tests never touch the real database."""
    import database as db

    monkeypatch.setattr(db, "get_pending_one_time_schedules", lambda: [])
    monkeypatch.setattr(db, "get_active_recurring_schedules", lambda: [])


@pytest.fixture(autouse=True)
def _config_minimal(monkeypatch):
    """Minimal config: prayer times and working hours disabled."""
    import services.slot_map_service as svc

    monkeypatch.setattr(
        svc,
        "load_config",
        lambda: {"prayer_times_enabled": False, "working_hours_enabled": False},
    )


# ── happy: _minutes_to_hhmm ──────────────────────────────────────────────────


class TestMinutesToHHMM:
    def test_midnight(self):
        assert _minutes_to_hhmm(0) == "00:00"

    def test_noon(self):
        assert _minutes_to_hhmm(720) == "12:00"

    def test_end_of_day(self):
        assert _minutes_to_hhmm(1439) == "23:59"

    def test_clamps_above_day(self):
        assert _minutes_to_hhmm(1441) == "23:59"

    def test_clamps_negative(self):
        assert _minutes_to_hhmm(-1) == "00:00"


# ── happy: get_day_slots ─────────────────────────────────────────────────────


class TestGetDaySlotsHappy:
    def test_returns_required_keys(self):
        result = get_day_slots("2026-03-27")
        assert {"date", "day_of_week", "working_hours", "slots"} <= result.keys()

    def test_date_echoed_back(self):
        assert get_day_slots("2026-03-27")["date"] == "2026-03-27"

    def test_empty_db_gives_empty_slots(self):
        assert get_day_slots("2026-03-27")["slots"] == []

    def test_weekday_friday(self):
        # 2026-03-27 is a Friday (weekday=4)
        assert get_day_slots("2026-03-27")["day_of_week"] == 4

    def test_working_hours_structure(self):
        wh = get_day_slots("2026-03-27")["working_hours"]
        assert "enabled" in wh and "start" in wh and "end" in wh


# ── happy: get_week_slots ────────────────────────────────────────────────────


class TestGetWeekSlotsHappy:
    def test_returns_seven_days(self):
        assert len(get_week_slots("2026-03-27")["days"]) == 7

    def test_none_date_returns_seven_days(self):
        assert len(get_week_slots(None)["days"]) == 7

    def test_all_days_have_required_keys(self):
        for day in get_week_slots("2026-03-27")["days"]:
            assert {"date", "slots"} <= day.keys()

    def test_week_starts_on_monday(self):
        from datetime import datetime

        first = get_week_slots("2026-03-27")["days"][0]["date"]
        assert datetime.strptime(first, "%Y-%m-%d").weekday() == 0

    def test_days_are_consecutive(self):
        from datetime import datetime, timedelta

        dates = [
            datetime.strptime(d["date"], "%Y-%m-%d")
            for d in get_week_slots("2026-03-27")["days"]
        ]
        for i in range(1, len(dates)):
            assert dates[i] - dates[i - 1] == timedelta(days=1)


# ── error: invalid inputs ─────────────────────────────────────────────────────


class TestGetDaySlotsError:
    def test_invalid_date_does_not_raise(self):
        result = get_day_slots("not-a-date")
        assert "slots" in result

    def test_empty_string_does_not_raise(self):
        result = get_day_slots("")
        assert "slots" in result

    def test_none_does_not_raise(self):
        result = get_day_slots(None)
        assert "slots" in result

    def test_wrong_format_does_not_raise(self):
        result = get_day_slots("27/03/2026")
        assert "slots" in result


# ── edge: date boundaries ────────────────────────────────────────────────────


class TestGetDaySlotsEdge:
    def test_leap_year_date(self):
        result = get_day_slots("2024-02-29")
        assert result["date"] == "2024-02-29"

    def test_year_end(self):
        result = get_day_slots("2024-12-31")
        assert result["date"] == "2024-12-31"

    def test_year_start(self):
        result = get_day_slots("2025-01-01")
        assert result["date"] == "2025-01-01"


class TestGetWeekSlotsEdge:
    def test_week_spanning_year_boundary(self):
        # 2024-12-31 (Tuesday) — week bleeds into 2025
        result = get_week_slots("2024-12-31")
        assert len(result["days"]) == 7
        dates = [d["date"] for d in result["days"]]
        assert any(d.startswith("2025") for d in dates)

    def test_leap_year_week(self):
        result = get_week_slots("2024-02-26")
        dates = [d["date"] for d in result["days"]]
        assert "2024-02-29" in dates


# ── security: adversarial date strings ───────────────────────────────────────


class TestGetDaySlotsSecurity:
    """All adversarial inputs must fall back gracefully — no crash, no leak."""

    def test_sql_injection_falls_back(self):
        result = get_day_slots("2026-03-27' OR '1'='1")
        assert "slots" in result

    def test_path_traversal_falls_back(self):
        result = get_day_slots("../../../etc/passwd")
        assert "slots" in result

    def test_xss_attempt_falls_back(self):
        result = get_day_slots("<script>alert(1)</script>")
        assert "slots" in result

    def test_very_long_string_falls_back(self):
        result = get_day_slots("A" * 10_000)
        assert "slots" in result

    def test_null_byte_falls_back(self):
        result = get_day_slots("2026-03-\x0027")
        assert "slots" in result

    def test_unicode_injection_falls_back(self):
        result = get_day_slots("2026\u202e03\u202e27")
        assert "slots" in result


# ── template: conflict badge removed ────────────────────────────────────────


class TestTimelineTemplate:
    def test_conflict_badge_text_not_in_template(self):
        """Müşteri kafasını karıştıran badge kaldırıldı."""
        tpl = os.path.join(os.path.dirname(__file__), "..", "templates", "_timeline.html")
        content = open(tpl, encoding="utf-8").read()
        assert "Çakışmalar anons kuyruğunda sıralı çözülür" not in content


# ── unit: _split_at_midnight ─────────────────────────────────────────────────


class TestSplitAtMidnight:
    MINS = 24 * 60  # 1440

    def test_normal_slot_stays_today(self):
        slot = RawSlot(600, 660, "recurring", "test.mp3")
        today, overflow = _split_at_midnight([slot])
        assert len(today) == 1
        assert len(overflow) == 0

    def test_overnight_slot_is_split(self):
        slot = RawSlot(1430, 1450, "recurring", "test.mp3")  # crosses midnight
        today, overflow = _split_at_midnight([slot])
        assert len(today) == 1
        assert len(overflow) == 1
        assert today[0].end_minute == self.MINS - 1
        assert overflow[0].start_minute == 0
        assert overflow[0].end_minute == 1450 - self.MINS

    def test_exact_midnight_end_stays_today(self):
        slot = RawSlot(1380, self.MINS, "recurring", "test.mp3")
        today, overflow = _split_at_midnight([slot])
        assert len(today) == 1
        assert len(overflow) == 0
