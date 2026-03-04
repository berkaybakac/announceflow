"""Faz 5 — StreamClient lifecycle tests (7 scenarios)."""
import os
import sys
from unittest.mock import MagicMock, patch, PropertyMock

import pytest

# Ensure agent package is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "agent"))

from stream_client import StreamClient


# --------------- helpers ---------------


def _make_live_process():
    """Return a mock subprocess that appears alive."""
    proc = MagicMock()
    proc.poll.return_value = None  # still running
    proc.pid = 12345
    return proc


def _make_dead_process(exit_code=1):
    """Return a mock subprocess that has already exited."""
    proc = MagicMock()
    proc.poll.return_value = exit_code
    proc.returncode = exit_code
    proc.pid = 12345
    return proc


# --------------- 1. start_sender success + idempotent ---------------


class TestStartSender:
    @patch("stream_client.subprocess.Popen")
    def test_start_sender_success(self, mock_popen):
        proc = _make_live_process()
        mock_popen.return_value = proc

        client = StreamClient()
        assert client.start_sender("192.168.1.10", 5800) is True
        assert client.is_alive() is True

        mock_popen.assert_called_once()
        cmd = mock_popen.call_args[0][0]
        assert "--stream-sender" in cmd
        assert "192.168.1.10" in cmd
        assert "5800" in cmd

    @patch("stream_client.subprocess.Popen")
    def test_start_sender_idempotent(self, mock_popen):
        """Already-running sender returns True without spawning again."""
        proc = _make_live_process()
        mock_popen.return_value = proc

        client = StreamClient()
        assert client.start_sender("host", 5800) is True
        assert client.start_sender("host", 5800) is True
        # Only one Popen call
        mock_popen.assert_called_once()


# --------------- 2. start_sender immediate death ---------------


    @patch("stream_client.subprocess.Popen")
    def test_start_sender_immediate_death(self, mock_popen):
        """Subprocess dies immediately -> False."""
        proc = _make_dead_process(exit_code=1)
        mock_popen.return_value = proc

        client = StreamClient()
        assert client.start_sender("host", 5800) is False
        assert client.is_alive() is False


# --------------- 3. stop_sender success + idempotent ---------------


class TestStopSender:
    @patch("stream_client.subprocess.Popen")
    def test_stop_sender_success(self, mock_popen):
        proc = _make_live_process()
        mock_popen.return_value = proc

        client = StreamClient()
        client.start_sender("host", 5800)
        assert client.stop_sender() is True
        assert client.is_alive() is False
        proc.terminate.assert_called_once()

    def test_stop_sender_idempotent(self):
        """Stopping when already stopped returns True."""
        client = StreamClient()
        assert client.stop_sender() is True


# --------------- 4. stop_sender timeout -> kill ---------------


    @patch("stream_client.subprocess.Popen")
    def test_stop_sender_kill_fallback(self, mock_popen):
        """Terminate timeout triggers kill."""
        import subprocess as sp

        proc = _make_live_process()
        proc.wait.side_effect = [sp.TimeoutExpired("cmd", 3), None]
        mock_popen.return_value = proc

        client = StreamClient()
        client.start_sender("host", 5800)
        assert client.stop_sender() is True
        proc.terminate.assert_called_once()
        proc.kill.assert_called_once()


# --------------- 5. is_alive states ---------------


class TestIsAlive:
    def test_is_alive_no_process(self):
        client = StreamClient()
        assert client.is_alive() is False

    @patch("stream_client.subprocess.Popen")
    def test_is_alive_running(self, mock_popen):
        proc = _make_live_process()
        mock_popen.return_value = proc

        client = StreamClient()
        client.start_sender("host", 5800)
        assert client.is_alive() is True

    @patch("stream_client.subprocess.Popen")
    def test_is_alive_dead_cleanup(self, mock_popen):
        """Dead process detected via poll -> cleaned up."""
        proc = _make_live_process()
        mock_popen.return_value = proc

        client = StreamClient()
        client.start_sender("host", 5800)
        assert client.is_alive() is True

        # Process dies
        proc.poll.return_value = 0
        assert client.is_alive() is False


# --------------- 6. _build_sender_cmd dev mode ---------------


class TestBuildSenderCmd:
    def test_dev_mode(self):
        """Dev mode: [sys.executable, agent.py, --stream-sender, host, port]."""
        cmd = StreamClient._build_sender_cmd("192.168.1.10", 5800)
        assert cmd[0] == sys.executable
        assert cmd[1].endswith("agent.py")
        assert cmd[2] == "--stream-sender"
        assert cmd[3] == "192.168.1.10"
        assert cmd[4] == "5800"


# --------------- 7. _build_sender_cmd frozen mode ---------------


    def test_frozen_mode(self):
        """Frozen EXE: [sys.executable, --stream-sender, host, port]."""
        with patch.object(sys, "frozen", True, create=True):
            cmd = StreamClient._build_sender_cmd("10.0.0.1", 5800)
        assert cmd[0] == sys.executable
        assert cmd[1] == "--stream-sender"
        assert cmd[2] == "10.0.0.1"
        assert cmd[3] == "5800"
        # No agent.py in frozen mode
        assert not any(c.endswith("agent.py") for c in cmd)
