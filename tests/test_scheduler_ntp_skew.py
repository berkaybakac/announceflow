"""Tests for _check_one_time_schedules() NTP clock skew handling.

Known limitation (Backlog: ntp_skew_v2):
    On Raspberry Pi / embedded devices, the system clock may be wrong at boot
    (no internet, RTC drift, or no RTC battery). When NTP syncs and jumps the
    clock FORWARD, a one-time schedule whose trigger window [0, 120 s] was
    skipped ends up with a large time_diff and gets silently cancelled.

    Mitigation (this release): cancel threshold raised 300 s → 600 s.
    Full fix deferred: ntp_skew_v2 (boot backfill + monotonic trigger tracking).

time_diff zones (time_diff = now - scheduled_dt):
    < -30          NTP backward sync / future schedule  → log warning, no action
    [-30, 0)       Minor clock jitter                   → no action, no log
    [0, 120]       Fire window                          → trigger schedule
    (120, 600]     Grace window                         → log info, no action (retry)
    > 600          Cancel threshold                     → log error, cancel in DB

How to diagnose "schedule never played":
    Search 'one_time_schedule_missed_cancelled' → time_diff >> 600 → NTP boot jump.
    Search 'one_time_schedule_grace_window' → was it in grace before cancel?
    Search 'one_time_schedule_clock_skew_negative' → NTP backward sync?
"""
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch, call

import pytest

from scheduler import Scheduler


# --------------- helpers ---------------

_SCHEDULED_DT = datetime(2026, 3, 28, 9, 0, 0, tzinfo=timezone.utc)
_SCHEDULE_ID = 42


def _make_scheduler() -> Scheduler:
    return Scheduler(check_interval_seconds=60)


def _make_pending_schedule(
    schedule_id: int = _SCHEDULE_ID,
    filename: str = "test_announcement.mp3",
    media_type: str = "music",
) -> dict:
    return {
        "id": schedule_id,
        "filename": filename,
        "filepath": f"/media/{filename}",
        "scheduled_datetime": "2026-03-28T09:00:00",
        "media_type": media_type,
        "media_id": None,
    }


def _run_check(monkeypatch, sched, time_diff_seconds: float, schedule: dict = None):
    """Run _check_one_time_schedules with a controlled time_diff."""
    if schedule is None:
        schedule = _make_pending_schedule()

    now = _SCHEDULED_DT + timedelta(seconds=time_diff_seconds)

    monkeypatch.setattr("scheduler.now_utc", lambda: now)
    monkeypatch.setattr(
        "scheduler.parse_storage_datetime_to_utc",
        lambda *a, **kw: _SCHEDULED_DT,
    )
    monkeypatch.setattr(
        "scheduler.db.get_pending_one_time_schedules",
        lambda: [schedule],
    )

    update_status = MagicMock()
    monkeypatch.setattr("scheduler.db.update_one_time_schedule_status", update_status)

    play_media = MagicMock(return_value=True)
    monkeypatch.setattr(sched, "_play_media", play_media)

    queue_ann = MagicMock()
    monkeypatch.setattr(sched, "_queue_announcement", queue_ann)

    sched._check_one_time_schedules(outside_working_hours=False, silence_blocked=False)
    return update_status, play_media, queue_ann


# --------------- fire window [0, 120] ---------------


class TestFireWindow:
    def test_fires_at_time_diff_zero(self, monkeypatch):
        """Schedule triggers immediately at time_diff = 0."""
        sched = _make_scheduler()
        update_status, play_media, _ = _run_check(monkeypatch, sched, 0)

        play_media.assert_called_once()
        update_status.assert_not_called()

    def test_fires_at_time_diff_60(self, monkeypatch):
        """Schedule triggers within fire window at time_diff = 60 s."""
        sched = _make_scheduler()
        update_status, play_media, _ = _run_check(monkeypatch, sched, 60)

        play_media.assert_called_once()

    def test_fires_at_boundary_120(self, monkeypatch):
        """Schedule triggers at the last second of the fire window (120 s)."""
        sched = _make_scheduler()
        update_status, play_media, _ = _run_check(monkeypatch, sched, 120)

        play_media.assert_called_once()

    def test_announcement_queued_not_played_directly(self, monkeypatch):
        """Announcements are queued, not played directly via _play_media."""
        sched = _make_scheduler()
        schedule = _make_pending_schedule(media_type="announcement")
        update_status, play_media, queue_ann = _run_check(
            monkeypatch, sched, 30, schedule
        )

        queue_ann.assert_called_once()
        play_media.assert_not_called()


# --------------- NTP backward sync: time_diff < 0 ---------------


class TestNTPBackwardSync:
    def test_no_action_when_time_diff_negative(self, monkeypatch):
        """Schedule is in the future — no action, no cancel."""
        sched = _make_scheduler()
        update_status, play_media, _ = _run_check(monkeypatch, sched, -60)

        update_status.assert_not_called()
        play_media.assert_not_called()

    def test_no_action_when_time_diff_slightly_negative(self, monkeypatch):
        """Minor clock jitter (< 30 s) is silently ignored."""
        sched = _make_scheduler()
        update_status, play_media, _ = _run_check(monkeypatch, sched, -10)

        update_status.assert_not_called()
        play_media.assert_not_called()

    def test_structured_log_emitted_when_significantly_in_future(self, monkeypatch):
        """log_schedule('one_time_schedule_clock_skew_negative') emitted for > 30 s."""
        sched = _make_scheduler()

        emitted_keys = []

        def _capture_log(key, _payload):
            emitted_keys.append(key)

        monkeypatch.setattr("scheduler.log_schedule", _capture_log)
        _run_check(monkeypatch, sched, -120)

        assert "one_time_schedule_clock_skew_negative" in emitted_keys, (
            "Expected structured log key 'one_time_schedule_clock_skew_negative' "
            "when schedule is > 30 s in the future (NTP backward sync diagnostic)"
        )

    def test_no_structured_log_for_minor_jitter(self, monkeypatch):
        """No log emitted for < 30 s jitter to avoid noise."""
        sched = _make_scheduler()

        emitted_keys = []

        def _capture_log(key, _payload):
            emitted_keys.append(key)

        monkeypatch.setattr("scheduler.log_schedule", _capture_log)
        _run_check(monkeypatch, sched, -20)

        assert "one_time_schedule_clock_skew_negative" not in emitted_keys

    def test_schedule_retried_after_backward_sync(self, monkeypatch):
        """After backward sync, schedule fires when clock reaches the window."""
        sched = _make_scheduler()

        # First tick: schedule is 90 s in the future (NTP backward sync)
        _run_check(monkeypatch, sched, -90)

        # Later tick: clock caught up, schedule is now 30 s past due
        update_status, play_media, _ = _run_check(monkeypatch, sched, 30)

        play_media.assert_called_once(), "Schedule must fire after clock catches up"


# --------------- Grace window (120, 600] ---------------


class TestGraceWindow:
    """Regression: old code cancelled at > 300 s. New code keeps alive until > 600 s.

    This matters for Pi devices with slow NTP boot sync: if NTP jumps the clock
    forward by 2-8 minutes, the schedule lands in the grace window instead of
    being immediately cancelled.
    """

    def test_no_cancel_at_time_diff_121(self, monkeypatch):
        """Just past fire window — NOT cancelled (grace period)."""
        sched = _make_scheduler()
        update_status, play_media, _ = _run_check(monkeypatch, sched, 121)

        update_status.assert_not_called()
        play_media.assert_not_called()

    def test_no_cancel_at_time_diff_300(self, monkeypatch):
        """Old cancel threshold (300 s) — must NOT cancel under new rules."""
        sched = _make_scheduler()
        update_status, _, _ = _run_check(monkeypatch, sched, 300)

        update_status.assert_not_called(), (
            "Schedule at 300 s must NOT be cancelled — "
            "threshold raised to 600 s for NTP boot sync mitigation"
        )

    def test_no_cancel_at_time_diff_599(self, monkeypatch):
        """Just inside grace window — NOT cancelled."""
        sched = _make_scheduler()
        update_status, _, _ = _run_check(monkeypatch, sched, 599)

        update_status.assert_not_called()

    def test_no_cancel_at_exact_boundary_600(self, monkeypatch):
        """Exactly at 600 s — still in grace (threshold is strictly > 600)."""
        sched = _make_scheduler()
        update_status, _, _ = _run_check(monkeypatch, sched, 600)

        update_status.assert_not_called()

    def test_structured_log_emitted_in_grace_window(self, monkeypatch):
        """log_schedule('one_time_schedule_grace_window') emitted in grace zone."""
        sched = _make_scheduler()

        emitted_keys = []

        def _capture_log(key, _payload):
            emitted_keys.append(key)

        monkeypatch.setattr("scheduler.log_schedule", _capture_log)
        _run_check(monkeypatch, sched, 250)

        assert "one_time_schedule_grace_window" in emitted_keys, (
            "Expected structured log key 'one_time_schedule_grace_window' "
            "when schedule is in (120, 600] zone"
        )


# --------------- Cancel threshold > 600 s ---------------


class TestCancelThreshold:
    def test_cancelled_at_time_diff_601(self, monkeypatch):
        """First second past cancel threshold — schedule cancelled."""
        sched = _make_scheduler()
        update_status, _, _ = _run_check(monkeypatch, sched, 601)

        update_status.assert_called_once_with(_SCHEDULE_ID, "cancelled")

    def test_cancelled_at_large_time_diff_ntp_boot_jump(self, monkeypatch):
        """NTP forward jump at boot (e.g. 3600 s) — schedule cancelled with error log.

        UNDESIRED SITUATION: this is the NTP skew bug. The schedule never played
        because NTP jumped the clock past the trigger window before the scheduler ran.
        'one_time_schedule_missed_cancelled' log with time_diff >> 600 is the signal.
        Full fix: ntp_skew_v2.
        """
        sched = _make_scheduler()

        emitted_errors = []

        def _capture_error(key, payload):
            emitted_errors.append((key, payload))

        monkeypatch.setattr("scheduler.log_error", _capture_error)
        update_status, _, _ = _run_check(monkeypatch, sched, 3600)

        update_status.assert_called_once_with(_SCHEDULE_ID, "cancelled")

        keys = [k for k, _ in emitted_errors]
        assert "one_time_schedule_missed_cancelled" in keys, (
            "UNDESIRED SITUATION: schedule cancelled due to NTP boot jump. "
            "Expected 'one_time_schedule_missed_cancelled' error log with "
            "time_diff_seconds ~3600. Deploy ntp_skew_v2 to fix."
        )

        cancelled_payload = next(p for k, p in emitted_errors if k == "one_time_schedule_missed_cancelled")
        assert cancelled_payload["time_diff_seconds"] == pytest.approx(3600, abs=1)
        assert cancelled_payload["backlog_ref"] == "ntp_skew_v2"

    def test_not_cancelled_when_already_queued(self, monkeypatch):
        """Schedule in _queued_one_time_ids is never cancelled, even past threshold."""
        sched = _make_scheduler()
        sched._queued_one_time_ids.add(_SCHEDULE_ID)

        update_status, _, _ = _run_check(monkeypatch, sched, 900)

        update_status.assert_not_called(), (
            "Schedule already queued for playback must not be cancelled "
            "even when time_diff > 600"
        )

    def test_error_log_contains_scheduled_at_and_now(self, monkeypatch):
        """Cancellation log payload includes scheduled_at and now_utc for diagnosis."""
        sched = _make_scheduler()

        emitted_errors = []

        def _capture_error(key, payload):
            emitted_errors.append((key, payload))

        monkeypatch.setattr("scheduler.log_error", _capture_error)
        _run_check(monkeypatch, sched, 700)

        assert emitted_errors, "Expected at least one log_error call"
        payload = next(
            (p for k, p in emitted_errors if k == "one_time_schedule_missed_cancelled"),
            None,
        )
        assert payload is not None
        assert "scheduled_at" in payload
        assert "now_utc" in payload
        assert "time_diff_seconds" in payload


# --------------- Boundary: old 300 s threshold regression ---------------


class TestOldThresholdRegression:
    """Explicit regression tests: old code cancelled at > 300 s.

    These document that the threshold change from 300 → 600 is intentional
    and that schedules between 300-600 s are now kept alive (grace window).
    """

    @pytest.mark.parametrize("time_diff", [301, 350, 400, 500, 600])
    def test_previously_cancelled_now_in_grace(self, monkeypatch, time_diff):
        """time_diff in (300, 600] was cancelled under old code — now kept alive."""
        sched = _make_scheduler()
        update_status, _, _ = _run_check(monkeypatch, sched, time_diff)

        update_status.assert_not_called(), (
            f"time_diff={time_diff}s: OLD code would cancel here. "
            "NEW code keeps schedule alive until > 600 s (NTP mitigation)."
        )
