"""Tests for _move_prayer_state_to_working_hours_if_needed().

Scenario: mesai ends BEFORE prayer starts (e.g. mesai 16:00, prayer 16:55).

    Timeline:
        16:00 — mesai ends   → _working_hours_pause_state = playlist@16:00
        16:55 — prayer starts → _prayer_pause_state        = playlist@16:55
        17:07 — prayer ends   → _move_prayer_state_to_working_hours_if_needed()

    Fix (Bug #6): prayer state (more recent) OVERWRITES working_hours state.
    Music resumes at next mesai start from prayer position, not the stale 16:00 position.

    Why prayer state is always more recent:
        _handle_working_hours() is skipped while prayer is active.
        Therefore working_hours slot can only have been set BEFORE prayer began.
"""
from unittest.mock import patch

import pytest

from scheduler import Scheduler


# --------------- helpers ---------------


def _make_scheduler() -> Scheduler:
    return Scheduler(check_interval_seconds=60)


def _make_state(index: int, playlist: list | None = None) -> dict:
    return {
        "playlist": playlist or ["a.mp3", "b.mp3", "c.mp3"],
        "index": index,
        "loop": True,
        "active": True,
    }


# --------------- normal overlap path (mesai ends DURING prayer) ---------------


class TestNormalOverlap:
    """Working hours end while prayer is already active — existing behaviour."""

    def test_prayer_state_moved_when_wh_slot_empty(self):
        """Normal path: prayer state is moved to working_hours slot when WH slot is empty."""
        sched = _make_scheduler()
        sched._prayer_pause_state = _make_state(index=5)
        sched._working_hours_pause_state = None

        sched._move_prayer_state_to_working_hours_if_needed()

        assert sched._working_hours_pause_state is not None
        assert sched._working_hours_pause_state["index"] == 5
        assert sched._prayer_pause_state is None

    def test_no_op_when_prayer_state_is_none(self):
        """No prayer state → function is a no-op."""
        sched = _make_scheduler()
        sched._prayer_pause_state = None
        sched._working_hours_pause_state = None

        sched._move_prayer_state_to_working_hours_if_needed()

        assert sched._working_hours_pause_state is None
        assert sched._prayer_pause_state is None


# --------------- edge case fix: mesai ended before prayer started ---------------


class TestWorkingHoursAlreadySet:
    """Bug #6 fix: prayer state overwrites stale working_hours state."""

    def test_prayer_state_overwrites_working_hours_state(self):
        """When WH slot already set, prayer state (more recent) replaces it.

        Old behaviour: prayer state was discarded → music resumed from stale position.
        New behaviour: prayer state overwrites WH slot → music resumes from correct position.
        """
        sched = _make_scheduler()
        sched._working_hours_pause_state = _make_state(index=2)  # stale (16:00)
        sched._prayer_pause_state = _make_state(index=7)          # more recent (16:55)

        sched._move_prayer_state_to_working_hours_if_needed()

        assert sched._working_hours_pause_state is not None
        assert sched._working_hours_pause_state["index"] == 7, (
            "Working hours slot must hold prayer index (7), not stale index (2). "
            "Music must resume from prayer position at next mesai start."
        )
        assert sched._prayer_pause_state is None

    def test_prayer_state_cleared_after_overwrite(self):
        """Prayer state is always cleared regardless of WH slot state."""
        sched = _make_scheduler()
        sched._working_hours_pause_state = _make_state(index=1)
        sched._prayer_pause_state = _make_state(index=9)

        sched._move_prayer_state_to_working_hours_if_needed()

        assert sched._prayer_pause_state is None

    def test_prayer_overwrites_preserves_playlist(self):
        """Playlist tracks from prayer state are preserved after overwrite."""
        prayer_playlist = ["x.mp3", "y.mp3", "z.mp3"]
        sched = _make_scheduler()
        sched._working_hours_pause_state = _make_state(index=0, playlist=["old.mp3"])
        sched._prayer_pause_state = _make_state(index=2, playlist=prayer_playlist)

        sched._move_prayer_state_to_working_hours_if_needed()

        assert sched._working_hours_pause_state["playlist"] == prayer_playlist

    def test_structured_log_emitted_on_overwrite(self):
        """prayer_state_overwrote_working_hours log key emitted (not the old error key)."""
        sched = _make_scheduler()
        sched._working_hours_pause_state = _make_state(index=2)
        sched._prayer_pause_state = _make_state(index=7)

        emitted_keys = []

        with patch("scheduler.log_prayer", side_effect=lambda k, _: emitted_keys.append(k)):
            sched._move_prayer_state_to_working_hours_if_needed()

        assert "prayer_state_overwrote_working_hours" in emitted_keys
        assert "prayer_state_lost_working_hours_already_set" not in emitted_keys, (
            "Old error log key must NOT be emitted — prayer state is no longer discarded"
        )

    def test_old_error_log_never_emitted(self):
        """prayer_state_lost_working_hours_already_set must never be emitted after fix."""
        sched = _make_scheduler()
        sched._working_hours_pause_state = _make_state(index=0)
        sched._prayer_pause_state = _make_state(index=5)

        error_keys = []

        with patch("scheduler.log_error", side_effect=lambda k, _: error_keys.append(k)):
            sched._move_prayer_state_to_working_hours_if_needed()

        assert "prayer_state_lost_working_hours_already_set" not in error_keys
