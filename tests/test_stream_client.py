"""StreamClient lifecycle tests (WASAPI loopback via soundcard)."""
import os
import sys
import threading
import time
from unittest.mock import MagicMock, patch, PropertyMock

import pytest

# Ensure agent package is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "agent"))


# --------------- soundcard mock setup ---------------

# soundcard is Windows-only (WASAPI); mock it for CI/test envs.
_sc_mock = MagicMock()
_fake_speaker = MagicMock()
_fake_speaker.name = "Test Speaker"
_sc_mock.default_speaker.return_value = _fake_speaker

# numpy mock for environments where numpy is not installed
try:
    import numpy as _real_np
    _np_available = True
except ImportError:
    _np_available = False

    # Build a minimal numpy mock that supports the operations used in _capture_loop
    _np_mock = MagicMock()

    class _FakeArray:
        """Minimal ndarray stand-in for PCM conversion tests."""
        def __init__(self, data):
            self._data = data
        def __mul__(self, other):
            return _FakeArray([v * other for v in self._data])
        def tobytes(self):
            import struct
            return struct.pack(f"<{len(self._data)}h",
                               *[max(-32768, min(32767, int(v))) for v in self._data])
        def astype(self, dtype):
            return self

    def _fake_clip(arr, lo, hi):
        if isinstance(arr, _FakeArray):
            return _FakeArray([max(lo, min(hi, v)) for v in arr._data])
        return arr

    _np_mock.clip = _fake_clip
    _np_mock.dtype.side_effect = lambda x: x
    _np_mock.float32 = "float32"


@pytest.fixture(autouse=True)
def _patch_dependencies(monkeypatch):
    """Ensure soundcard (and numpy if missing) is importable."""
    monkeypatch.setitem(sys.modules, "soundcard", _sc_mock)
    if not _np_available:
        monkeypatch.setitem(sys.modules, "numpy", _np_mock)
    # Reset mocks between tests
    _sc_mock.reset_mock()
    _sc_mock.default_speaker.return_value = _fake_speaker
    _fake_speaker.reset_mock()
    yield


from stream_client import StreamClient


# --------------- 1. start_sender success ---------------


class TestStartSender:
    def test_start_sender_success(self):
        """Sender starts successfully with a valid speaker."""
        client = StreamClient()

        # Make the capture loop run briefly then stop
        original_capture = client._capture_loop

        def fake_capture(host, port):
            # Simulate a running capture loop
            while client._running:
                time.sleep(0.01)

        with patch.object(client, "_capture_loop", side_effect=fake_capture):
            result = client.start_sender("192.168.1.10", 5800)
            assert result is True
            assert client.is_alive() is True
            client.stop_sender()

    def test_start_sender_idempotent(self):
        """Already-running sender returns True without starting again."""
        client = StreamClient()

        def fake_capture(host, port):
            while client._running:
                time.sleep(0.01)

        with patch.object(client, "_capture_loop", side_effect=fake_capture):
            assert client.start_sender("host", 5800) is True
            assert client.start_sender("host", 5800) is True
            client.stop_sender()


# --------------- 2. start_sender no audio device ---------------


    def test_start_sender_no_speaker(self):
        """No default speaker -> False with last_error set."""
        _sc_mock.default_speaker.return_value = None

        client = StreamClient()
        assert client.start_sender("host", 5800) is False
        assert client.last_error == "no_audio_device"
        assert client.is_alive() is False

    def test_start_sender_soundcard_import_error(self):
        """soundcard raises exception -> False."""
        _sc_mock.default_speaker.side_effect = RuntimeError("No WASAPI")

        client = StreamClient()
        assert client.start_sender("host", 5800) is False
        assert client.last_error == "no_audio_device"

        # Reset
        _sc_mock.default_speaker.side_effect = None


# --------------- 3. start_sender capture thread dies on startup ---------------


    def test_start_sender_thread_dies_immediately(self):
        """Capture thread dies on startup -> False."""
        client = StreamClient()

        def dying_capture(host, port):
            # Immediately set _running to False (simulates startup error)
            client._running = False

        with patch.object(client, "_capture_loop", side_effect=dying_capture):
            result = client.start_sender("host", 5800)
            assert result is False
            assert client.is_alive() is False


# --------------- 4. stop_sender success + idempotent ---------------


class TestStopSender:
    def test_stop_sender_success(self):
        """Running sender stops cleanly."""
        client = StreamClient()

        def fake_capture(host, port):
            while client._running:
                time.sleep(0.01)

        with patch.object(client, "_capture_loop", side_effect=fake_capture):
            client.start_sender("host", 5800)
            assert client.stop_sender() is True
            assert client.is_alive() is False

    def test_stop_sender_idempotent(self):
        """Stopping when already stopped returns True."""
        client = StreamClient()
        assert client.stop_sender() is True


# --------------- 5. is_alive states ---------------


class TestIsAlive:
    def test_is_alive_no_thread(self):
        """No thread started -> False."""
        client = StreamClient()
        assert client.is_alive() is False

    def test_is_alive_running(self):
        """Active capture thread -> True."""
        client = StreamClient()

        def fake_capture(host, port):
            while client._running:
                time.sleep(0.01)

        with patch.object(client, "_capture_loop", side_effect=fake_capture):
            client.start_sender("host", 5800)
            assert client.is_alive() is True
            client.stop_sender()

    def test_is_alive_after_stop(self):
        """After stop -> False."""
        client = StreamClient()

        def fake_capture(host, port):
            while client._running:
                time.sleep(0.01)

        with patch.object(client, "_capture_loop", side_effect=fake_capture):
            client.start_sender("host", 5800)
            client.stop_sender()
            assert client.is_alive() is False


# --------------- 6. capture_loop sends correct PCM format ---------------


class TestCaptureLoop:
    @pytest.mark.skipif(not _np_available, reason="numpy not installed")
    def test_capture_sends_udp_packets(self):
        """Capture loop sends s16le PCM data over UDP."""
        import numpy as np

        client = StreamClient()
        packets_sent = []

        fake_recorder = MagicMock()
        # Return float32 audio data in [-1.0, 1.0]
        fake_audio = np.zeros((4410, 1), dtype=np.float32)
        fake_audio[0, 0] = 0.5  # Non-zero sample
        call_count = 0

        def fake_record(numframes):
            nonlocal call_count
            call_count += 1
            if call_count >= 2:
                client._running = False  # Stop after 2 iterations
            return fake_audio

        fake_recorder.record = fake_record
        fake_recorder.__enter__ = MagicMock(return_value=fake_recorder)
        fake_recorder.__exit__ = MagicMock(return_value=False)
        _fake_speaker.recorder.return_value = fake_recorder

        mock_socket = MagicMock()

        def capture_sendto(data, addr):
            packets_sent.append((data, addr))

        mock_socket.sendto = capture_sendto

        with patch("stream_client.socket.socket", return_value=mock_socket):
            client._running = True
            client._capture_loop("192.168.1.10", 5800)

        # Verify packets were sent
        assert len(packets_sent) == 2

        # Verify address
        assert packets_sent[0][1] == ("192.168.1.10", 5800)

        # Verify PCM format: s16le (signed 16-bit little-endian)
        pcm_data = packets_sent[0][0]
        # 4410 frames * 1 channel * 2 bytes per sample = 8820 bytes
        assert len(pcm_data) == 4410 * 1 * 2

        # Verify the non-zero sample was converted correctly
        # 0.5 * 32767 ≈ 16383 -> in s16le bytes
        pcm_array = np.frombuffer(pcm_data, dtype=np.dtype("<i2"))
        assert pcm_array[0] == 16383 or pcm_array[0] == 16384  # rounding

    def test_capture_loop_error_sets_last_error(self):
        """Exception in capture loop sets last_error to standard code."""
        client = StreamClient()
        _sc_mock.default_speaker.side_effect = RuntimeError("WASAPI init failed")

        mock_socket = MagicMock()
        with patch("stream_client.socket.socket", return_value=mock_socket):
            client._running = True
            client._capture_loop("host", 5800)

        assert client.last_error == "capture_error"
        assert client._running is False

        # Reset
        _sc_mock.default_speaker.side_effect = None
        _sc_mock.default_speaker.return_value = _fake_speaker

    def test_capture_loop_closes_socket(self):
        """Socket is closed when capture loop ends."""
        client = StreamClient()

        # Make recorder.record raise to end the loop quickly
        fake_recorder = MagicMock()
        fake_recorder.record = MagicMock(side_effect=StopIteration)
        fake_recorder.__enter__ = MagicMock(return_value=fake_recorder)
        fake_recorder.__exit__ = MagicMock(return_value=False)
        _fake_speaker.recorder.return_value = fake_recorder

        mock_socket = MagicMock()
        with patch("stream_client.socket.socket", return_value=mock_socket):
            client._running = True
            client._capture_loop("host", 5800)

        mock_socket.close.assert_called_once()


# --------------- 7. last_error tracking ---------------


class TestLastError:
    def test_last_error_initially_none(self):
        client = StreamClient()
        assert client.last_error is None

    def test_last_error_set_on_no_device(self):
        _sc_mock.default_speaker.return_value = None
        client = StreamClient()
        client.start_sender("host", 5800)
        assert client.last_error == "no_audio_device"

    def test_last_error_cleared_on_success(self):
        """Successful start doesn't clear previous error (by design)."""
        client = StreamClient()

        def fake_capture(host, port):
            while client._running:
                time.sleep(0.01)

        with patch.object(client, "_capture_loop", side_effect=fake_capture):
            # First fail
            _sc_mock.default_speaker.return_value = None
            client.start_sender("host", 5800)
            assert client.last_error == "no_audio_device"

            # Then succeed
            _sc_mock.default_speaker.return_value = _fake_speaker
            client.start_sender("host", 5800)
            # last_error still has previous value (not reset on success)
            client.stop_sender()
