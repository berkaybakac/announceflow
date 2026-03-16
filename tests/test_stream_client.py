"""StreamClient lifecycle tests (WASAPI loopback via soundcard)."""
import os
import socket
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
_fake_loopback_mic = MagicMock()
_fake_loopback_mic.name = "Test Loopback Mic"
_fake_loopback_mic.isloopback = True


class _NoRecorderSpeaker:
    """Real-world-like speaker object without recorder()."""

    def __init__(self, name="NoRecorder Speaker", speaker_id="speaker-1"):
        self.name = name
        self.id = speaker_id


class _FakeJoinThread:
    """Minimal thread stub with controllable alive state."""

    def __init__(self, alive=True):
        self._alive = alive
        self.join_calls = []

    def is_alive(self):
        return self._alive

    def join(self, timeout=None):
        self.join_calls.append(timeout)

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
    _sc_mock.get_microphone.return_value = _fake_loopback_mic
    _sc_mock.get_microphone.side_effect = None
    _sc_mock.all_microphones.return_value = [_fake_loopback_mic]
    _sc_mock.all_microphones.side_effect = None
    _fake_speaker.reset_mock()
    _fake_loopback_mic.reset_mock()
    yield


from stream_client import StreamClient
import stream_client as stream_client_module


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
            assert client.start_sender("127.0.0.1", 5800) is True
            assert client.start_sender("127.0.0.1", 5800) is True
            client.stop_sender()

    def test_start_rejected_when_previous_capture_thread_alive(self):
        """Fail-safe: block restart when stale capture thread is still alive."""
        client = StreamClient()
        stale = _FakeJoinThread(alive=True)
        client._thread = stale
        client._running = False

        assert client.start_sender("127.0.0.1", 5800) is False
        assert client.last_error == "capture_thread_stuck"
        assert client._thread is stale

    def test_start_sender_uses_loopback_microphone_when_speaker_has_no_recorder(self):
        """When speaker lacks recorder(), client should resolve loopback microphone."""
        client = StreamClient()
        _sc_mock.default_speaker.return_value = _NoRecorderSpeaker(
            "NoRecorder Speaker", "speaker-xyz"
        )

        def fake_capture(host, port):
            while client._running:
                time.sleep(0.01)

        with patch.object(client, "_capture_loop", side_effect=fake_capture):
            result = client.start_sender("127.0.0.1", 5800)
            assert result is True
            assert _sc_mock.get_microphone.called
            snap = client.get_attempt_snapshot()
            assert snap["capture_device_name"] == "Test Loopback Mic"
            client.stop_sender()


# --------------- 2. start_sender no audio device ---------------


    def test_start_sender_no_speaker(self):
        """No default speaker -> False with last_error set."""
        _sc_mock.default_speaker.return_value = None

        client = StreamClient()
        assert client.start_sender("127.0.0.1", 5800) is False
        assert client.last_error == "no_audio_device"
        assert client.is_alive() is False

    def test_start_sender_soundcard_import_error(self):
        """soundcard raises exception -> False."""
        _sc_mock.default_speaker.side_effect = RuntimeError("No WASAPI")

        client = StreamClient()
        assert client.start_sender("127.0.0.1", 5800) is False
        assert client.last_error == "no_audio_device"

        # Reset
        _sc_mock.default_speaker.side_effect = None

    def test_start_sender_fails_when_no_loopback_recorder_resolved(self):
        """No speaker.recorder + no loopback device => recorder_open_failed."""
        _sc_mock.default_speaker.return_value = _NoRecorderSpeaker("No Loopback Speaker")
        _sc_mock.get_microphone.side_effect = RuntimeError("loopback lookup failed")
        _sc_mock.all_microphones.return_value = []

        client = StreamClient()
        assert client.start_sender("127.0.0.1", 5800) is False
        assert client.last_error == "recorder_open_failed"
        snap = client.get_attempt_snapshot()
        assert snap["error_code"] == "recorder_open_failed"
        _sc_mock.get_microphone.side_effect = None

    def test_start_sender_rejects_non_loopback_microphone_fallback(self):
        """Do not pick a plain physical mic as capture fallback."""
        _sc_mock.default_speaker.return_value = _NoRecorderSpeaker("Main Speakers")
        _sc_mock.get_microphone.side_effect = RuntimeError("loopback lookup failed")
        non_loopback = MagicMock()
        non_loopback.name = "Internal Microphone"
        non_loopback.isloopback = False
        _sc_mock.all_microphones.return_value = [non_loopback]

        client = StreamClient()
        assert client.start_sender("127.0.0.1", 5800) is False
        assert client.last_error == "recorder_open_failed"
        snap = client.get_attempt_snapshot()
        errs = "\n".join((e.get("error") or "") for e in (snap.get("open_errors") or []))
        assert "No loopback microphone matched default speaker" in errs

        _sc_mock.get_microphone.side_effect = None


# --------------- 3. start_sender capture thread dies on startup ---------------


    def test_start_sender_thread_dies_immediately(self):
        """Capture thread dies on startup -> False."""
        client = StreamClient()

        def dying_capture(host, port):
            # Immediately set _running to False (simulates startup error)
            client._running = False

        with patch.object(client, "_capture_loop", side_effect=dying_capture):
            result = client.start_sender("127.0.0.1", 5800)
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
            client.start_sender("127.0.0.1", 5800)
            assert client.stop_sender() is True
            assert client.is_alive() is False

    def test_stop_sender_idempotent(self):
        """Stopping when already stopped returns True."""
        client = StreamClient()
        assert client.stop_sender() is True

    def test_stop_sender_timeout_sets_capture_thread_stuck(self):
        """Timeout on join should mark capture_thread_stuck and keep stale thread ref."""
        client = StreamClient()
        stale = _FakeJoinThread(alive=True)
        client._thread = stale
        client._running = True
        client._new_attempt("127.0.0.1", 5800)

        assert client.stop_sender() is False
        assert stale.join_calls == [3]
        assert client.last_error == "capture_thread_stuck"
        snap = client.get_attempt_snapshot()
        assert snap["error_code"] == "capture_thread_stuck"
        assert client._thread is stale

    def test_no_restart_until_stale_thread_clears(self):
        """Start stays blocked while stale thread alive, resumes once thread is dead."""
        client = StreamClient()
        stale = _FakeJoinThread(alive=True)
        client._thread = stale
        client._running = False

        assert client.start_sender("127.0.0.1", 5800) is False
        assert client.last_error == "capture_thread_stuck"

        stale._alive = False

        def fake_capture(host, port):
            while client._running:
                time.sleep(0.01)

        with patch.object(client, "_capture_loop", side_effect=fake_capture):
            assert client.start_sender("127.0.0.1", 5800) is True
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
            client.start_sender("127.0.0.1", 5800)
            assert client.is_alive() is True
            client.stop_sender()

    def test_is_alive_after_stop(self):
        """After stop -> False."""
        client = StreamClient()

        def fake_capture(host, port):
            while client._running:
                time.sleep(0.01)

        with patch.object(client, "_capture_loop", side_effect=fake_capture):
            client.start_sender("127.0.0.1", 5800)
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
        call_count = 0

        def fake_record(numframes):
            nonlocal call_count
            call_count += 1
            if call_count >= 2:
                client._running = False  # Stop after 2 iterations
            # Return float32 audio data in [-1.0, 1.0]
            fake_audio = np.zeros((numframes, 1), dtype=np.float32)
            fake_audio[0, 0] = 0.5  # Non-zero sample
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
        assert len(packets_sent) >= 1

        # Verify address
        assert packets_sent[0][1] == ("192.168.1.10", 5800)

        # Verify PCM format: s16le (signed 16-bit little-endian)
        pcm_data = packets_sent[0][0]
        # _BLOCK_SIZE frames * 1 channel * 2 bytes per sample
        assert len(pcm_data) == stream_client_module._BLOCK_SIZE * 2

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

    @pytest.mark.skipif(not _np_available, reason="numpy not installed")
    def test_capture_loop_uses_loopback_microphone_when_speaker_has_no_recorder(self):
        """Regression: real _Speaker may not expose recorder(); use loopback microphone."""
        import numpy as np

        client = StreamClient()
        packets_sent = []
        _sc_mock.default_speaker.return_value = _NoRecorderSpeaker(
            "NoRecorder Speaker", "speaker-xyz"
        )

        fake_recorder = MagicMock()
        call_count = 0

        def fake_record(numframes):
            nonlocal call_count
            call_count += 1
            if call_count >= 2:
                client._running = False
            return np.zeros((numframes, 1), dtype=np.float32)

        fake_recorder.record = fake_record
        fake_recorder.__enter__ = MagicMock(return_value=fake_recorder)
        fake_recorder.__exit__ = MagicMock(return_value=False)
        _fake_loopback_mic.recorder.return_value = fake_recorder

        mock_socket = MagicMock()

        def capture_sendto(data, addr):
            packets_sent.append((data, addr))

        mock_socket.sendto = capture_sendto

        with patch("stream_client.socket.socket", return_value=mock_socket):
            client._running = True
            client._capture_loop("192.168.1.10", 5800)

        assert _sc_mock.get_microphone.called
        assert len(packets_sent) >= 1

    @pytest.mark.skipif(not _np_available, reason="numpy not installed")
    def test_capture_loop_falls_back_to_48000_and_resamples(self, monkeypatch):
        """If 44100 open fails, fallback rate should still emit 44100-sized packets."""
        import numpy as np

        client = StreamClient()
        packets_sent = []
        call_count = 0

        def recorder_factory(*, samplerate, channels, blocksize):
            if samplerate == 44100:
                raise RuntimeError("44100 open failed")
            fake_recorder = MagicMock()

            def fake_record(numframes):
                nonlocal call_count
                call_count += 1
                if call_count >= 2:
                    client._running = False
                return np.zeros((numframes, 1), dtype=np.float32)

            fake_recorder.record = fake_record
            fake_recorder.__enter__ = MagicMock(return_value=fake_recorder)
            fake_recorder.__exit__ = MagicMock(return_value=False)
            return fake_recorder

        _fake_speaker.recorder.side_effect = recorder_factory
        monkeypatch.setattr(
            stream_client_module, "_CAPTURE_RATE_CANDIDATES", (44100, 48000)
        )

        mock_socket = MagicMock()
        mock_socket.sendto.side_effect = lambda data, addr: packets_sent.append((data, addr))

        with patch("stream_client.socket.socket", return_value=mock_socket):
            client._new_attempt("192.168.1.10", 5800)
            client._running = True
            client._capture_loop("192.168.1.10", 5800)

        snap = client.get_attempt_snapshot()
        assert snap["capture_sample_rate"] == 48000
        assert snap["sample_rate"] == stream_client_module._TARGET_SAMPLE_RATE
        assert len(packets_sent) >= 1
        assert all(
            len(payload) == stream_client_module._BLOCK_SIZE * 2
            for payload, _ in packets_sent
        )
        _fake_speaker.recorder.side_effect = None


# --------------- 7. last_error tracking ---------------


class TestLastError:
    def test_last_error_initially_none(self):
        client = StreamClient()
        assert client.last_error is None

    def test_last_error_set_on_no_device(self):
        _sc_mock.default_speaker.return_value = None
        client = StreamClient()
        client.start_sender("127.0.0.1", 5800)
        assert client.last_error == "no_audio_device"

    def test_last_error_cleared_on_success(self):
        """Successful start should clear stale error state from previous failures."""
        client = StreamClient()

        def fake_capture(host, port):
            while client._running:
                time.sleep(0.01)

        with patch.object(client, "_capture_loop", side_effect=fake_capture):
            # First fail
            _sc_mock.default_speaker.return_value = None
            client.start_sender("127.0.0.1", 5800)
            assert client.last_error == "no_audio_device"

            # Then succeed
            _sc_mock.default_speaker.return_value = _fake_speaker
            client.start_sender("127.0.0.1", 5800)
            assert client.last_error is None
            client.stop_sender()


# --------------- 8. diagnostics and error-code coverage ---------------


class TestDiagnostics:
    def test_attempt_snapshot_success_path(self):
        """Successful start/stop should finalize attempt snapshot with success=True."""
        client = StreamClient()

        def fake_capture(host, port):
            while client._running:
                time.sleep(0.01)

        with patch.object(client, "_capture_loop", side_effect=fake_capture):
            assert client.start_sender("127.0.0.1", 5800) is True
            snap_running = client.get_attempt_snapshot()
            assert snap_running["attempt_id"]
            assert snap_running["target_host"] == "127.0.0.1"
            assert snap_running["resolved_host"] == "127.0.0.1"
            assert snap_running["stage"] in {"steady_capture", "capture_thread_start"}

            client.stop_sender()
            snap_done = client.get_attempt_snapshot()
            assert snap_done["success"] is True
            assert snap_done["error_code"] is None

    def test_start_sender_resolve_failure_falls_back_to_raw_host(self):
        """Host resolve errors should not block startup; sender falls back to raw host."""
        client = StreamClient()
        def fake_capture(host, port):
            while client._running:
                time.sleep(0.01)

        with patch(
            "stream_client.socket.gethostbyname",
            side_effect=socket.gaierror("name not known"),
        ), patch.object(client, "_capture_loop", side_effect=fake_capture):
            assert client.start_sender("nonexistent-host", 5800) is True
            snap = client.get_attempt_snapshot()
            assert snap["resolved_host"] == "nonexistent-host"
            assert snap["error_code"] is None
            stages = [entry["stage"] for entry in snap["stages"]]
            assert "host_resolve_warning" in stages
            client.stop_sender()

    @pytest.mark.skipif(not _np_available, reason="numpy not installed")
    def test_capture_loop_udp_send_failure_sets_udp_send_failed(self):
        """Network send errors must produce udp_send_failed code."""
        import numpy as np

        client = StreamClient()
        fake_recorder = MagicMock()
        fake_recorder.record = MagicMock(
            return_value=np.zeros((stream_client_module._BLOCK_SIZE, 1), dtype=np.float32)
        )
        fake_recorder.__enter__ = MagicMock(return_value=fake_recorder)
        fake_recorder.__exit__ = MagicMock(return_value=False)
        _fake_speaker.recorder.return_value = fake_recorder

        mock_socket = MagicMock()
        mock_socket.sendto.side_effect = OSError("network is unreachable")

        with patch("stream_client.socket.socket", return_value=mock_socket):
            client._new_attempt("127.0.0.1", 5800)
            client._running = True
            client._capture_loop("127.0.0.1", 5800)

        assert client.last_error == "udp_send_failed"
        snap = client.get_attempt_snapshot()
        assert snap["error_code"] == "udp_send_failed"
        assert snap["success"] is False

    def test_capture_loop_recorder_open_failure_sets_specific_code(self):
        """Recorder open failure should map to recorder_open_failed code."""
        client = StreamClient()
        _fake_speaker.recorder.side_effect = RuntimeError("open failed")

        mock_socket = MagicMock()
        with patch("stream_client.socket.socket", return_value=mock_socket):
            client._new_attempt("127.0.0.1", 5800)
            client._running = True
            client._capture_loop("127.0.0.1", 5800)

        assert client.last_error == "recorder_open_failed"
        snap = client.get_attempt_snapshot()
        assert snap["error_code"] == "recorder_open_failed"
        assert snap["success"] is False
        assert "open failed" in (snap.get("traceback") or "")
        assert len(snap.get("open_errors") or []) >= 1
        assert (snap.get("open_errors") or [])[-1]["error"] == "open failed"

        _fake_speaker.recorder.side_effect = None

    def test_build_failure_report_includes_attempt_and_error_code(self):
        """Failure report should contain attempt_id and error code for triage."""
        client = StreamClient()
        _sc_mock.default_speaker.return_value = None
        client.start_sender("127.0.0.1", 5800)

        report = client.build_failure_report()
        assert "attempt_id=" in report
        assert "error_code=no_audio_device" in report
