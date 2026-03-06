"""StreamManager unit tests for correlation-id environment forwarding."""

import subprocess
import time

import pytest

from stream_manager import StreamManager


class _AliveProc:
    def __init__(self):
        self.pid = 4242
        self.returncode = None
        self._terminated = False

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


@pytest.fixture
def fake_popen(monkeypatch):
    captured = {}
    proc = _AliveProc()

    def _fake_popen(cmd, stdout=None, stderr=None, env=None):
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

    def _fake_popen(cmd, stdout=None, stderr=None, env=None):
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
