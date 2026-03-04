"""Faz 5/6 — StreamClient lifecycle tests (ffmpeg-based sender)."""
import os
import sys
from unittest.mock import MagicMock, patch

import pytest

# Ensure agent package is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "agent"))

from stream_client import StreamClient, discover_loopback_device, _find_ffmpeg


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
    @patch("stream_client.discover_loopback_device", return_value="Stereo Mix")
    @patch("stream_client.subprocess.Popen")
    def test_start_sender_success(self, mock_popen, mock_discover):
        proc = _make_live_process()
        mock_popen.return_value = proc

        client = StreamClient()
        assert client.start_sender("192.168.1.10", 5800) is True
        assert client.is_alive() is True

        mock_popen.assert_called_once()
        cmd = mock_popen.call_args[0][0]
        assert "ffmpeg" in cmd[0]
        assert "audio=Stereo Mix" in " ".join(cmd)
        assert "192.168.1.10" in " ".join(cmd)

    @patch("stream_client.discover_loopback_device", return_value="Stereo Mix")
    @patch("stream_client.subprocess.Popen")
    def test_start_sender_idempotent(self, mock_popen, mock_discover):
        """Already-running sender returns True without spawning again."""
        proc = _make_live_process()
        mock_popen.return_value = proc

        client = StreamClient()
        assert client.start_sender("host", 5800) is True
        assert client.start_sender("host", 5800) is True
        # Only one Popen call
        mock_popen.assert_called_once()


# --------------- 2. start_sender immediate death ---------------


    @patch("stream_client.discover_loopback_device", return_value="Stereo Mix")
    @patch("stream_client.subprocess.Popen")
    def test_start_sender_immediate_death(self, mock_popen, mock_discover):
        """Subprocess dies immediately -> False."""
        proc = _make_dead_process(exit_code=1)
        mock_popen.return_value = proc

        client = StreamClient()
        assert client.start_sender("host", 5800) is False
        assert client.is_alive() is False


# --------------- 3. start_sender no loopback device ---------------


    @patch("stream_client.discover_loopback_device", return_value=None)
    def test_start_sender_no_device(self, mock_discover):
        """No loopback device found -> False."""
        client = StreamClient()
        assert client.start_sender("host", 5800) is False


# --------------- 4. stop_sender success + idempotent ---------------


class TestStopSender:
    @patch("stream_client.discover_loopback_device", return_value="Stereo Mix")
    @patch("stream_client.subprocess.Popen")
    def test_stop_sender_success(self, mock_popen, mock_discover):
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


# --------------- 5. stop_sender timeout -> kill ---------------


    @patch("stream_client.discover_loopback_device", return_value="Stereo Mix")
    @patch("stream_client.subprocess.Popen")
    def test_stop_sender_kill_fallback(self, mock_popen, mock_discover):
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


# --------------- 6. is_alive states ---------------


class TestIsAlive:
    def test_is_alive_no_process(self):
        client = StreamClient()
        assert client.is_alive() is False

    @patch("stream_client.discover_loopback_device", return_value="Stereo Mix")
    @patch("stream_client.subprocess.Popen")
    def test_is_alive_running(self, mock_popen, mock_discover):
        proc = _make_live_process()
        mock_popen.return_value = proc

        client = StreamClient()
        client.start_sender("host", 5800)
        assert client.is_alive() is True

    @patch("stream_client.discover_loopback_device", return_value="Stereo Mix")
    @patch("stream_client.subprocess.Popen")
    def test_is_alive_dead_cleanup(self, mock_popen, mock_discover):
        """Dead process detected via poll -> cleaned up."""
        proc = _make_live_process()
        mock_popen.return_value = proc

        client = StreamClient()
        client.start_sender("host", 5800)
        assert client.is_alive() is True

        # Process dies
        proc.poll.return_value = 0
        assert client.is_alive() is False


# --------------- 7. _build_sender_cmd structure ---------------


class TestBuildSenderCmd:
    @patch("stream_client.discover_loopback_device", return_value="Stereo Mix")
    def test_cmd_contains_ffmpeg_and_device(self, mock_discover):
        """Command should contain ffmpeg, dshow, device name, and target."""
        cmd = StreamClient._build_sender_cmd("192.168.1.10", 5800)
        assert len(cmd) > 0
        assert "ffmpeg" in cmd[0]
        assert "-f" in cmd
        assert "dshow" in cmd
        joined = " ".join(cmd)
        assert "audio=Stereo Mix" in joined
        assert "192.168.1.10" in joined
        assert "5800" in joined

    @patch("stream_client.discover_loopback_device", return_value=None)
    def test_cmd_empty_when_no_device(self, mock_discover):
        """No device -> empty list."""
        cmd = StreamClient._build_sender_cmd("host", 5800)
        assert cmd == []


# --------------- 8. discover_loopback_device ---------------


class TestDiscoverLoopback:
    @patch("stream_client.subprocess.run")
    def test_known_name_found(self, mock_run):
        """First known name succeeds."""
        result = MagicMock()
        result.stderr = b"some output without error"
        mock_run.return_value = result

        device = discover_loopback_device("ffmpeg")
        assert device == "Stereo Mix"

    @patch("stream_client.subprocess.run")
    def test_known_names_all_fail(self, mock_run):
        """All known names fail -> None (no fallback to arbitrary device)."""
        result = MagicMock()
        result.stderr = b"Could not find audio device"
        mock_run.return_value = result

        device = discover_loopback_device("ffmpeg")
        assert device is None


# --------------- 9. _find_ffmpeg ---------------


class TestFindFfmpeg:
    def test_dev_mode(self):
        """Dev mode returns 'ffmpeg' (from PATH)."""
        assert _find_ffmpeg() == "ffmpeg"

    @patch("os.path.isfile", return_value=True)
    def test_frozen_mode(self, mock_isfile):
        """Frozen mode returns bundled path."""
        with patch.object(sys, "frozen", True, create=True):
            with patch.object(sys, "_MEIPASS", "/tmp/meipass", create=True):
                result = _find_ffmpeg()
        assert "ffmpeg.exe" in result
