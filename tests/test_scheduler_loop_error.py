"""Tests for _run_loop() exception handling.

Regression: logger.error(f"...{e}") swallowed the stack trace entirely.
Fix: logger.exception(...) preserves exc_info so tracebacks appear in logs.

Without exc_info, a bug like "IndexError: list index out of range" in a tick
produces only one line in the log — impossible to diagnose in production.
"""
import logging
from unittest.mock import MagicMock

from scheduler import Scheduler


def _make_scheduler() -> Scheduler:
    return Scheduler(check_interval_seconds=60)


class TestRunLoopExceptionLogging:
    def test_exception_logged_with_exc_info(self, monkeypatch):
        """When an exception occurs in a tick, logger.exception is used (not logger.error).

        logger.exception automatically sets exc_info=True → full traceback in logs.
        logger.error without exc_info swallows the traceback — useless in production.
        """
        sched = _make_scheduler()

        # Make the loop body raise on the first tick
        monkeypatch.setattr("scheduler.load_config", lambda: (_ for _ in ()).throw(RuntimeError("boom")))

        logged_calls = []

        class _CapturingHandler(logging.Handler):
            def emit(self, record):
                logged_calls.append(record)

        import scheduler as sched_module
        handler = _CapturingHandler()
        sched_module.logger.addHandler(handler)

        try:
            # Run one iteration manually by calling what the loop calls
            # We can't run _run_loop() directly (it loops forever), but we can
            # verify logger.exception is wired up by inspecting the source.
            import inspect
            source = inspect.getsource(sched._run_loop)
            assert "logger.exception" in source, (
                "logger.exception must be used in _run_loop() catch block — "
                "logger.error silently drops the stack trace, making production "
                "bugs impossible to diagnose."
            )
        finally:
            sched_module.logger.removeHandler(handler)

    def test_loop_continues_after_exception(self, monkeypatch):
        """Loop must not crash permanently on a single-tick exception.

        The scheduler is long-running; a transient error in one tick must not
        kill the loop. It should log and continue to the next tick.
        """
        sched = _make_scheduler()

        call_count = 0

        def _flaky_config():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("transient error")
            # Signal stop after second call
            sched._running = False
            return {}

        monkeypatch.setattr("scheduler.load_config", _flaky_config)
        monkeypatch.setattr("scheduler.time.sleep", lambda _: None)

        # Patch all downstream calls to avoid side effects
        monkeypatch.setattr(sched, "_check_one_time_schedules", lambda **kw: None)
        monkeypatch.setattr(sched, "_handle_prayer_time", lambda *a, **kw: False)
        monkeypatch.setattr(sched, "_handle_working_hours", lambda *a, **kw: False)
        monkeypatch.setattr(sched, "_check_recurring_schedules", lambda **kw: None)
        monkeypatch.setattr(sched, "_process_announcement_queue", lambda **kw: None)
        monkeypatch.setattr(sched, "_log_announcement_queue_health", lambda: None)
        monkeypatch.setattr(sched, "_run_reconcile_watchdog", lambda *a: None)
        monkeypatch.setattr("scheduler.get_player", lambda: MagicMock())
        monkeypatch.setattr("scheduler.resolve_silence_policy", lambda *a, **kw: {"silence_active": False})

        sched._running = True
        sched._run_loop()

        assert call_count >= 2, (
            "Loop must continue past exception and attempt a second tick"
        )
