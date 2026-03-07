"""StreamManager unit tests for correlation-id environment forwarding."""

import threading
import subprocess
import time
from unittest.mock import patch

import pytest

from stream_manager import StreamManager


class _FakeStdin:
    """Minimal stdin mock that supports close() without error."""

    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


class _AliveProc:
    def __init__(self):
        self.pid = 4242
        self.returncode = None
        self._terminated = False
        self.stdin = _FakeStdin()

    def poll(self):
        return None if not self._terminated else 0

    def terminate(self):
        self._terminated = True

    def wait(self, timeout=None):
        return 0

    def kill(self):
        self._terminated = True


class _TimeoutProc(_AliveProc):
    """Simulates a process that refuses to exit until killed."""

    def __init__(self):
        super().__init__()
        self.killed = False

    def wait(self, timeout=None):
        if not self.killed:
            raise subprocess.TimeoutExpired(cmd="receiver", timeout=timeout)
        self._terminated = True
        return 0

    def kill(self):
        self.killed = True
        self._terminated = True


class _DeadProc:
    """Simulates a receiver process that exits immediately."""

    def __init__(self, returncode=1):
        self.pid = 9898
        self.returncode = returncode
        self.stdin = _FakeStdin()

    def poll(self):
        return self.returncode


@pytest.fixture
def fake_popen(monkeypatch):
    captured = {}
    proc = _AliveProc()

    def _fake_popen(cmd, stdin=None, stdout=None, stderr=None, env=None):
        captured["cmd"] = cmd
        captured["env"] = env
        return proc

    monkeypatch.setattr("stream_manager.time.sleep", lambda _x: None)
    monkeypatch.setattr("stream_manager.subprocess.Popen", _fake_popen)
    return captured


@pytest.fixture
def fake_popen_timeout(monkeypatch):
    captured = {}
    proc = _TimeoutProc()

    def _fake_popen(cmd, stdin=None, stdout=None, stderr=None, env=None):
        captured["cmd"] = cmd
        captured["env"] = env
        captured["proc"] = proc
        return proc

    monkeypatch.setattr("stream_manager.time.sleep", lambda _x: None)
    monkeypatch.setattr("stream_manager.subprocess.Popen", _fake_popen)
    return captured


def test_start_receiver_sets_correlation_id_env(monkeypatch, fake_popen):
    monkeypatch.delenv("ANNOUNCEFLOW_STREAM_CORRELATION_ID", raising=False)
    mgr = StreamManager(port=5800)
    assert mgr.start_receiver(correlation_id="cid-manager-1") is True
    assert fake_popen["env"]["ANNOUNCEFLOW_STREAM_CORRELATION_ID"] == "cid-manager-1"
    assert mgr.stop_receiver() is True


def test_start_receiver_without_correlation_id_does_not_set_env(monkeypatch, fake_popen):
    monkeypatch.delenv("ANNOUNCEFLOW_STREAM_CORRELATION_ID", raising=False)
    mgr = StreamManager(port=5800)
    assert mgr.start_receiver() is True
    assert "ANNOUNCEFLOW_STREAM_CORRELATION_ID" not in fake_popen["env"]
    assert mgr.stop_receiver() is True


def test_stop_receiver_forces_kill_on_timeout(monkeypatch, fake_popen_timeout):
    monkeypatch.delenv("ANNOUNCEFLOW_STREAM_CORRELATION_ID", raising=False)
    mgr = StreamManager(port=5800)
    assert mgr.start_receiver() is True
    assert mgr.stop_receiver() is True
    # Background thread handles the kill; wait briefly for it to complete
    deadline = time.monotonic() + 2
    while not fake_popen_timeout["proc"].killed and time.monotonic() < deadline:
        time.sleep(0.05)
    assert fake_popen_timeout["proc"].killed is True


def test_start_receiver_concurrent_calls_spawn_once(monkeypatch):
    proc = _AliveProc()
    popen_calls = {"count": 0}
    first_call_entered = threading.Event()
    release_first_call = threading.Event()

    def _fake_popen(cmd, stdin=None, stdout=None, stderr=None, env=None):
        popen_calls["count"] += 1
        first_call_entered.set()
        release_first_call.wait(timeout=1)
        return proc

    monkeypatch.setattr("stream_manager.time.sleep", lambda _x: None)
    monkeypatch.setattr("stream_manager.subprocess.Popen", _fake_popen)

    mgr = StreamManager(port=5800)
    results = []

    t1 = threading.Thread(target=lambda: results.append(mgr.start_receiver()))
    t2 = threading.Thread(target=lambda: results.append(mgr.start_receiver()))
    t1.start()
    assert first_call_entered.wait(timeout=1) is True
    t2.start()
    release_first_call.set()
    t1.join(timeout=1)
    t2.join(timeout=1)

    assert sorted(results) == [True, True]
    assert popen_calls["count"] == 1


def test_start_failure_counter_warns_on_third_and_resets_on_success(monkeypatch):
    outcomes = [
        _DeadProc(11), _DeadProc(11),  # start #1 fails
        _DeadProc(12), _DeadProc(12),  # start #2 fails
        _DeadProc(13), _DeadProc(13),  # start #3 fails -> threshold warn
        _AliveProc(),                   # start #4 succeeds
    ]
    popen_calls = {"count": 0}

    def _fake_popen(cmd, stdin=None, stdout=None, stderr=None, env=None):
        popen_calls["count"] += 1
        return outcomes.pop(0)

    monkeypatch.setattr("stream_manager.time.sleep", lambda _x: None)
    monkeypatch.setattr("stream_manager.subprocess.Popen", _fake_popen)

    mgr = StreamManager(port=5800)
    with patch("stream_manager.logger.warning") as mock_warn:
        assert mgr.start_receiver() is False
        assert mgr.start_receiver() is False
        assert mgr.start_receiver() is False

        assert mgr._consecutive_start_failures == 3
        threshold_calls = [
            c
            for c in mock_warn.call_args_list
            if c.args and "consecutive start failures" in c.args[0]
        ]
        assert len(threshold_calls) == 1

        assert mgr.start_receiver() is True
        assert mgr._consecutive_start_failures == 0

    assert popen_calls["count"] == 7


def test_start_rejected_while_previous_stop_in_progress(monkeypatch):
    class _StuckOnTerminateProc(_AliveProc):
        def __init__(self):
            super().__init__()
            self.killed = False

        def terminate(self):
            # Simulate process ignoring SIGTERM.
            pass

        def wait(self, timeout=None):
            if not self.killed:
                raise subprocess.TimeoutExpired(cmd="receiver", timeout=timeout)
            self._terminated = True
            return 0

        def kill(self):
            self.killed = True
            self._terminated = True

    proc = _StuckOnTerminateProc()

    def _fake_popen(cmd, stdin=None, stdout=None, stderr=None, env=None):
        return proc

    monkeypatch.setattr("stream_manager.time.sleep", lambda _x: None)
    monkeypatch.setattr("stream_manager.subprocess.Popen", _fake_popen)
    monkeypatch.setattr(
        StreamManager,
        "_background_kill",
        lambda self, _proc: time.sleep(0.2),
    )

    mgr = StreamManager(port=5800)
    assert mgr.start_receiver() is True
    assert mgr.stop_receiver() is True
    assert mgr.start_receiver() is False


def test_stop_receiver_closes_stdin_before_terminate(monkeypatch):
    """Verify stop closes stdin pipe before sending SIGTERM for graceful ffmpeg exit."""
    call_order = []

    class _OrderTrackingProc(_AliveProc):
        def __init__(self):
            super().__init__()
            self.stdin = _FakeStdin()
            # Override close to track order
            original_close = self.stdin.close
            def tracked_close():
                call_order.append("stdin_close")
                original_close()
            self.stdin.close = tracked_close

        def terminate(self):
            call_order.append("terminate")
            super().terminate()

    proc = _OrderTrackingProc()

    def _fake_popen(cmd, stdin=None, stdout=None, stderr=None, env=None):
        return proc

    monkeypatch.setattr("stream_manager.time.sleep", lambda _x: None)
    monkeypatch.setattr("stream_manager.subprocess.Popen", _fake_popen)

    mgr = StreamManager(port=5800)
    assert mgr.start_receiver() is True
    assert mgr.stop_receiver() is True
    assert call_order == ["stdin_close", "terminate"]
