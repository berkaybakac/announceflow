"""Tests for announcement resume worker threading.Event coordination.

Regression 1 (2026-03-16): _resume_stream_after_announcement_worker previously used
`while player.is_playing: sleep(0.5)`. If music restarted in the gap
between poll iterations, the worker looped forever and blocked all
subsequent resumes (observed: 2.5-hour block, 28 "already running"
warnings in production logs from 12:51–15:26 on 2026-03-16).

Fix: _run_restore_worker signals _announcement_done (threading.Event)
when announcement playback ends; resume worker waits on it with a 120 s
timeout instead of polling is_playing.

Regression 2 (event-overwrite race): When two announcements play back-to-back,
_play_media() creates a new threading.Event and assigns it to self._announcement_done
for each announcement. If _run_restore_worker reads self._announcement_done at runtime
(STEP 2) instead of using the event captured at spawn time, it signals the SECOND
announcement's Event, leaving the first resume worker waiting 120 s on an orphaned Event.

Fix: _play_media() creates done_event locally and passes it to _start_restore_worker()
→ _run_restore_worker() via args. Workers use the passed done_event, never
self._announcement_done at runtime.
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
        """_run_restore_worker signals the done_event passed at spawn time.

        Fix for event-overwrite race: the worker now uses done_event parameter
        instead of reading self._announcement_done at runtime. This ensures the
        correct event is signalled even if self._announcement_done is overwritten
        by a concurrent announcement before this worker reaches STEP 2.
        """
        sched = _make_scheduler()

        done = threading.Event()
        # done_event is passed directly — self._announcement_done is irrelevant now
        sched._announcement_done = None  # deliberately left None to confirm worker uses param

        player = MagicMock()
        player.is_playing = False  # Already idle → inner while exits immediately

        # No state queued → worker breaks right after signalling
        sched._restore_target_state = None

        t = threading.Thread(
            target=sched._run_restore_worker, args=(player, done), daemon=True
        )
        t.start()
        t.join(timeout=2.0)

        assert done.is_set(), "_run_restore_worker must signal the passed done_event"
        assert sched._announcement_done is None  # shared ref cleared regardless

    def test_restore_worker_does_not_double_signal(self):
        """If the event is already set, _run_restore_worker leaves it as-is (idempotent)."""
        sched = _make_scheduler()

        done = threading.Event()
        done.set()

        player = MagicMock()
        player.is_playing = False
        sched._restore_target_state = None

        t = threading.Thread(
            target=sched._run_restore_worker, args=(player, done), daemon=True
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
        """When player stops before timeout, worker signals done_event normally."""
        sched = _make_scheduler()
        sched._RESTORE_PLAYER_WAIT_TIMEOUT_S = 30  # generous — player already idle

        done = threading.Event()
        sched._restore_target_state = None  # no state → breaks after signalling done

        player = MagicMock()
        player.is_playing = False  # Already stopped

        t = threading.Thread(target=sched._run_restore_worker, args=(player, done), daemon=True)
        t.start()
        t.join(timeout=2.0)

        assert not t.is_alive()
        assert done.is_set(), "Normal path must still signal done_event"


# --------------- Regression 2: event-overwrite race ---------------


class TestEventOverwriteRace:
    """Regression: back-to-back announcements must not leave stream paused 120 s.

    Old code: _run_restore_worker read self._announcement_done at STEP 2 (runtime).
    When announcement B overwrote self._announcement_done before restore_worker_A
    reached STEP 2, restore_worker_A signalled Event_B instead of Event_A.
    resume_worker_A was waiting on Event_A → 120 s timeout.

    New code: done_event is captured locally in _play_media() and passed through
    _start_restore_worker() → _run_restore_worker() via args. self._announcement_done
    is never read inside the worker.
    """

    def test_restore_worker_signals_own_event_not_self_attribute(self):
        """Worker signals the passed done_event even when self._announcement_done differs.

        Simulates the race: self._announcement_done is set to Event_B (second announcement)
        BEFORE the worker reaches STEP 2. Old code would signal Event_B; new code signals
        the passed Event_A.
        """
        sched = _make_scheduler()
        player = MagicMock()
        player.is_playing = False
        sched._restore_target_state = None

        event_a = threading.Event()  # event for announcement A (passed to worker)
        event_b = threading.Event()  # event for announcement B (overwrites self attr)

        # Simulate the race: self._announcement_done already points to B's event
        sched._announcement_done = event_b

        t = threading.Thread(
            target=sched._run_restore_worker, args=(player, event_a), daemon=True
        )
        t.start()
        t.join(timeout=2.0)

        # Worker must signal the event IT was spawned with (A), not the one on self (B)
        assert event_a.is_set(), "Worker must signal the passed done_event (Event_A)"
        assert not event_b.is_set(), "Worker must NOT signal Event_B (belongs to a different announcement)"

    def test_restore_worker_clears_self_announcement_done_regardless(self):
        """self._announcement_done is always cleared to None after STEP 2.

        This prevents stale Event references from accumulating on self even when
        done_event=None (e.g. non-stream-interrupting announcements).
        """
        sched = _make_scheduler()
        player = MagicMock()
        player.is_playing = False
        sched._restore_target_state = None

        stale_event = threading.Event()
        sched._announcement_done = stale_event

        t = threading.Thread(
            target=sched._run_restore_worker, args=(player, None), daemon=True
        )
        t.start()
        t.join(timeout=2.0)

        assert sched._announcement_done is None, "self._announcement_done must be cleared even when done_event=None"

    def test_two_back_to_back_restore_workers_both_signal_correct_events(self):
        """Two restore workers each signal their own done_event independently.

        Simulates what happens when _play_media() is called twice: worker A gets
        Event_A, worker B gets Event_B. Each signals only its own event.
        """
        sched = _make_scheduler()
        player = MagicMock()
        player.is_playing = False

        event_a = threading.Event()
        event_b = threading.Event()

        # Worker A: no restore state → exits after STEP 2
        sched._restore_target_state = None
        sched._announcement_done = event_a

        t_a = threading.Thread(
            target=sched._run_restore_worker, args=(player, event_a), daemon=True
        )
        t_a.start()
        t_a.join(timeout=2.0)

        # Worker B: same setup, own event
        sched._restore_target_state = None
        sched._announcement_done = event_b

        t_b = threading.Thread(
            target=sched._run_restore_worker, args=(player, event_b), daemon=True
        )
        t_b.start()
        t_b.join(timeout=2.0)

        assert event_a.is_set(), "Event_A must be signalled by worker A"
        assert event_b.is_set(), "Event_B must be signalled by worker B"

    def test_resume_worker_unblocks_within_deadline_when_restore_worker_uses_correct_event(
        self, monkeypatch
    ):
        """End-to-end: resume_worker_A unblocks promptly when restore_worker_A signals Event_A.

        Old code: restore_worker_A signals Event_B (overwritten), resume_worker_A waits
        120 s on Event_A. New code: restore_worker_A signals Event_A → resume_worker_A
        exits within milliseconds.
        """
        sched = _make_scheduler()
        player = MagicMock()
        player.is_playing = False

        _patch_worker_deps(monkeypatch, sched, player)

        event_a = threading.Event()
        sched._announcement_done = event_a
        sched._stream_resume_worker_in_progress = True

        # Simulate restore_worker_A completing: it signals Event_A (correct behaviour)
        def _restore_worker_a():
            time.sleep(0.05)
            event_a.set()  # Correctly signals Event_A

        threading.Thread(target=_restore_worker_a, daemon=True).start()

        t0 = time.monotonic()
        sched._resume_stream_after_announcement_worker()
        elapsed = time.monotonic() - t0

        # Must unblock in well under 1 s, not wait for 120 s timeout
        assert elapsed < 2.0, f"resume worker took {elapsed:.2f}s — expected < 2s"
        assert sched._stream_resume_worker_in_progress is False
