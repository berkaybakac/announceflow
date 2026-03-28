"""Tests for announcement resume worker threading.Event coordination.

Regression: _resume_stream_after_announcement_worker previously used
`while player.is_playing: sleep(0.5)`. If music restarted in the gap
between poll iterations, the worker looped forever and blocked all
subsequent resumes (observed: 2.5-hour block, 28 "already running"
warnings in production logs from 12:51–15:26 on 2026-03-16).

Fix: _run_restore_worker signals _announcement_done (threading.Event)
when announcement playback ends; resume worker waits on it with a 120 s
timeout instead of polling is_playing.
"""
import threading
import time
from unittest.mock import MagicMock

import pytest

from scheduler import Scheduler


# --------------- helpers ---------------


def _make_scheduler() -> Scheduler:
    return Scheduler(check_interval_seconds=60)


def _patch_worker_deps(monkeypatch, sched, player, *, stream_alive=True):
    """Patch all external calls inside _resume_stream_after_announcement_worker."""
    svc = MagicMock()
    svc.policy_sender_alive.return_value = stream_alive
    monkeypatch.setattr("scheduler.get_player", lambda: player)
    monkeypatch.setattr("scheduler.get_stream_service", lambda: svc)
    monkeypatch.setattr(sched, "_get_cached_config", lambda: {})
    monkeypatch.setattr(
        "scheduler.resolve_silence_policy",
        lambda *a, **kw: {"silence_active": False},
    )
    monkeypatch.setattr("scheduler.should_force_stop_stream", lambda _: False)
    monkeypatch.setattr("scheduler.should_resume_stream", lambda *a: True)
    return svc


# --------------- tests ---------------


class TestResumeWorkerEventCoordination:
    def test_worker_exits_when_event_set(self, monkeypatch):
        """Resume worker exits promptly once _announcement_done is signalled.

        Old code: `while player.is_playing` → loops forever when music restarts.
        New code: event.wait(120) → exits as soon as restore worker signals.
        """
        sched = _make_scheduler()
        player = MagicMock()
        player.is_playing = True  # Would loop forever under old code

        _patch_worker_deps(monkeypatch, sched, player)

        sched._announcement_done = threading.Event()
        sched._stream_resume_worker_in_progress = True

        # Signal the event from a background thread after a short delay
        def _signal():
            time.sleep(0.05)
            sched._announcement_done.set()

        threading.Thread(target=_signal, daemon=True).start()

        t0 = time.monotonic()
        sched._resume_stream_after_announcement_worker()
        elapsed = time.monotonic() - t0

        # Must complete in well under 1 s (not stuck for 120 s timeout)
        assert elapsed < 2.0
        assert sched._stream_resume_worker_in_progress is False

    def test_worker_proceeds_immediately_when_event_pre_set(self, monkeypatch):
        """If the event was already set before the worker starts, no waiting."""
        sched = _make_scheduler()
        player = MagicMock()
        player.is_playing = True  # Irrelevant — event already set

        _patch_worker_deps(monkeypatch, sched, player)

        done = threading.Event()
        done.set()
        sched._announcement_done = done
        sched._stream_resume_worker_in_progress = True

        t0 = time.monotonic()
        sched._resume_stream_after_announcement_worker()
        elapsed = time.monotonic() - t0

        assert elapsed < 1.0
        assert sched._stream_resume_worker_in_progress is False

    def test_worker_fallback_poll_when_no_event(self, monkeypatch):
        """Fallback bounded poll runs when _announcement_done is None.

        This path should not occur in normal operation, but must not hang.
        """
        sched = _make_scheduler()
        player = MagicMock()
        player.is_playing = False  # Already idle → fallback exits at once

        _patch_worker_deps(monkeypatch, sched, player)

        sched._announcement_done = None
        sched._stream_resume_worker_in_progress = True

        t0 = time.monotonic()
        sched._resume_stream_after_announcement_worker()
        elapsed = time.monotonic() - t0

        assert elapsed < 1.0
        assert sched._stream_resume_worker_in_progress is False

    def test_restore_worker_signals_announcement_done(self):
        """_run_restore_worker sets _announcement_done when music stops."""
        sched = _make_scheduler()

        done = threading.Event()
        sched._announcement_done = done

        player = MagicMock()
        player.is_playing = False  # Already idle → inner while exits immediately

        # No state queued → worker breaks right after signalling
        sched._restore_target_state = None

        t = threading.Thread(
            target=sched._run_restore_worker, args=(player,), daemon=True
        )
        t.start()
        t.join(timeout=2.0)

        assert done.is_set(), "_run_restore_worker must signal _announcement_done"
        assert sched._announcement_done is None  # Cleared after set

    def test_restore_worker_does_not_double_signal(self):
        """If the event is already set, _run_restore_worker leaves it as-is."""
        sched = _make_scheduler()

        done = threading.Event()
        done.set()
        sched._announcement_done = done

        player = MagicMock()
        player.is_playing = False
        sched._restore_target_state = None

        t = threading.Thread(
            target=sched._run_restore_worker, args=(player,), daemon=True
        )
        t.start()
        t.join(timeout=2.0)

        # Event stays set — idempotent
        assert done.is_set()

    def test_guard_blocks_duplicate_resume_worker_starts(self, monkeypatch):
        """_stream_resume_worker_in_progress flag prevents concurrent workers."""
        sched = _make_scheduler()
        player = MagicMock()
        player.is_playing = False

        _patch_worker_deps(monkeypatch, sched, player)

        done = threading.Event()
        done.set()  # Unblock the worker immediately
        sched._announcement_done = done

        result1 = sched._start_stream_resume_worker_after_announcement()
        # Second call while first thread hasn't finished yet (flag still True)
        result2 = sched._start_stream_resume_worker_after_announcement()

        assert result1 is True
        assert result2 is False

    def test_resume_worker_calls_stream_resume_on_success(self, monkeypatch):
        """On normal exit (event set, stream alive), resume_after_announcement called."""
        sched = _make_scheduler()
        player = MagicMock()
        player.is_playing = False

        svc = _patch_worker_deps(monkeypatch, sched, player)

        done = threading.Event()
        done.set()
        sched._announcement_done = done
        sched._stream_resume_worker_in_progress = True

        sched._resume_stream_after_announcement_worker()

        svc.resume_after_announcement.assert_called_once()

    def test_resume_worker_does_not_resume_during_silence_policy(self, monkeypatch):
        """If silence policy is active after announcement, stream stays stopped."""
        sched = _make_scheduler()
        player = MagicMock()
        player.is_playing = False

        svc = _patch_worker_deps(monkeypatch, sched, player)
        monkeypatch.setattr("scheduler.should_force_stop_stream", lambda _: True)

        done = threading.Event()
        done.set()
        sched._announcement_done = done
        sched._stream_resume_worker_in_progress = True

        sched._resume_stream_after_announcement_worker()

        svc.force_stop_by_policy.assert_called_once()
        svc.resume_after_announcement.assert_not_called()


class TestRestoreWorkerTimeout:
    """Regression: _run_restore_worker had no timeout on the is_playing poll loop.

    If mpg123 hung and player.is_playing never cleared, the restore thread
    would loop forever at sleep(0.5), blocking all future playlist restores.

    Fix: added _RESTORE_PLAYER_WAIT_TIMEOUT_S (120 s) deadline; worker logs
    an error and returns (triggering finally-block cleanup) when exceeded.
    """

    def test_worker_exits_when_player_hangs(self):
        """Hung player (is_playing stays True) must not block the thread forever."""
        sched = _make_scheduler()
        sched._RESTORE_PLAYER_WAIT_TIMEOUT_S = 0  # instant timeout for test speed

        player = MagicMock()
        player.is_playing = True  # Never stops — looped forever before fix

        sched._restore_target_state = None
        sched._announcement_done = None

        t = threading.Thread(target=sched._run_restore_worker, args=(player,), daemon=True)
        t.start()
        t.join(timeout=2.0)

        assert not t.is_alive(), "Worker must exit after timeout, not loop forever"

    def test_worker_timeout_resets_restore_in_progress(self):
        """After a timeout abort, _restore_in_progress is cleared and thread removed."""
        sched = _make_scheduler()
        sched._RESTORE_PLAYER_WAIT_TIMEOUT_S = 0

        player = MagicMock()
        player.is_playing = True

        restore_thread = threading.Thread(
            target=sched._run_restore_worker, args=(player,), daemon=True
        )
        with sched._restore_lock:
            sched._restore_threads.append(restore_thread)
            sched._restore_in_progress = True

        restore_thread.start()
        restore_thread.join(timeout=2.0)

        assert not restore_thread.is_alive()
        assert sched._restore_in_progress is False, "finally block must clear flag"
        assert restore_thread not in sched._restore_threads, "finally block must deregister thread"

    def test_worker_normal_flow_unaffected(self):
        """When player stops before timeout, worker proceeds normally."""
        sched = _make_scheduler()
        sched._RESTORE_PLAYER_WAIT_TIMEOUT_S = 30  # generous — player already idle

        done = threading.Event()
        sched._announcement_done = done
        sched._restore_target_state = None  # no state → breaks after signalling done

        player = MagicMock()
        player.is_playing = False  # Already stopped

        t = threading.Thread(target=sched._run_restore_worker, args=(player,), daemon=True)
        t.start()
        t.join(timeout=2.0)

        assert not t.is_alive()
        assert done.is_set(), "Normal path must still signal _announcement_done"
