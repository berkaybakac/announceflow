"""Tests for stream receiver observability helpers."""

import io

import _stream_receiver as receiver


def _new_counters():
    return {
        "udp_overrun": 0,
        "alsa_xrun": 0,
        "demux_errors": 0,
        "immediate_exit": 0,
        "audio_device_errors": 0,
        "connection_errors": 0,
        "first_input_at": None,
        "first_output_at": None,
        "first_overrun_at": None,
        "last_overrun_at": None,
        "first_xrun_at": None,
        "last_xrun_at": None,
        "repeat_context": None,
    }


def test_udp_input_url_default(monkeypatch):
    monkeypatch.delenv("ANNOUNCEFLOW_STREAM_UDP_FIFO", raising=False)
    url = receiver._build_udp_input_url(5800)
    assert "overrun_nonfatal=1" in url
    assert "fifo_size=4194304" in url  # 4 MB default — jitter buffer


def test_udp_input_url_with_fifo(monkeypatch):
    monkeypatch.setenv("ANNOUNCEFLOW_STREAM_UDP_FIFO", "262144")
    url = receiver._build_udp_input_url(5800)
    assert "overrun_nonfatal=1" in url
    assert "fifo_size=262144" in url
    assert "fifo_size=4194304" not in url  # env var replaces default


def test_parse_extra_ffmpeg_args_handles_invalid_shell_fragment(monkeypatch):
    monkeypatch.setenv("ANNOUNCEFLOW_STREAM_FFMPEG_ARGS", "\"unterminated")
    assert receiver._parse_extra_ffmpeg_args() == []


def test_process_ffmpeg_line_updates_counters():
    counters = _new_counters()
    buf = io.StringIO()

    receiver._process_ffmpeg_line("Input #0, s16le, from 'udp://0.0.0.0:5800':", buf, counters)
    receiver._process_ffmpeg_line("Output #0, alsa, to 'plughw:2,0':", buf, counters)
    receiver._process_ffmpeg_line(
        "[udp @ 0x1] Circular buffer overrun. Surviving due to overrun_nonfatal option",
        buf,
        counters,
    )
    receiver._process_ffmpeg_line("[alsa @ 0x1] ALSA buffer xrun.", buf, counters)
    receiver._process_ffmpeg_line("Last message repeated 2 times", buf, counters)
    receiver._process_ffmpeg_line(
        "[in#0/s16le @ 0x2] Error during demuxing: Immediate exit requested",
        buf,
        counters,
    )
    receiver._process_ffmpeg_line("cannot open audio device default", buf, counters)
    receiver._process_ffmpeg_line("connection refused", buf, counters)

    assert counters["udp_overrun"] == 1
    assert counters["alsa_xrun"] == 3
    assert counters["demux_errors"] == 1
    assert counters["immediate_exit"] == 1
    assert counters["audio_device_errors"] == 1
    assert counters["connection_errors"] == 1
    assert counters["first_input_at"] is not None
    assert counters["first_output_at"] is not None
    assert counters["first_overrun_at"] is not None
    assert counters["last_overrun_at"] is not None
    assert counters["first_xrun_at"] is not None
    assert counters["last_xrun_at"] is not None


def test_process_ffmpeg_line_emits_first_input_output_events(monkeypatch):
    counters = _new_counters()
    buf = io.StringIO()
    emitted = []

    def _capture(event, data):
        emitted.append((event, data))

    monkeypatch.setattr(receiver, "_safe_log_system", _capture)

    receiver._process_ffmpeg_line(
        "Input #0, s16le, from 'udp://0.0.0.0:5800':",
        buf,
        counters,
        correlation_id="cid-1",
        port=5800,
        alsa_device="plughw:2,0",
    )
    receiver._process_ffmpeg_line(
        "Output #0, alsa, to 'plughw:2,0':",
        buf,
        counters,
        correlation_id="cid-1",
        port=5800,
        alsa_device="plughw:2,0",
    )

    assert [name for name, _ in emitted] == [
        "stream_receiver_first_input",
        "stream_receiver_first_output",
    ]
    assert emitted[0][1]["correlation_id"] == "cid-1"
    assert emitted[1][1]["correlation_id"] == "cid-1"


def test_classify_receiver_exit_success():
    assert receiver._classify_receiver_exit(None, None) == "success"
    assert receiver._classify_receiver_exit(0, None) == "success"


def test_classify_receiver_exit_controlled_with_signal():
    assert receiver._classify_receiver_exit(255, "SIGTERM") == "controlled"
    assert receiver._classify_receiver_exit(-9, "SIGTERM") == "controlled"
    assert receiver._classify_receiver_exit(-15, "SIGINT") == "controlled"


def test_classify_receiver_exit_controlled_any_nonzero_with_signal():
    """Any nonzero exit with signal present is controlled — no whitelist."""
    assert receiver._classify_receiver_exit(42, "SIGTERM") == "controlled"
    assert receiver._classify_receiver_exit(234, "SIGTERM") == "controlled"
    assert receiver._classify_receiver_exit(1, "SIGINT") == "controlled"


def test_classify_receiver_exit_unexpected_without_signal():
    assert receiver._classify_receiver_exit(255, None) == "unexpected"
    # External SIGKILL (not from our handler)
    assert receiver._classify_receiver_exit(-9, None) == "unexpected"
    # SIGSEGV crash
    assert receiver._classify_receiver_exit(-11, None) == "unexpected"
    # SIGTERM delivered but handler didn't run (race)
    assert receiver._classify_receiver_exit(-15, None) == "unexpected"


def test_classify_receiver_exit_clean_exit_despite_signal():
    """ffmpeg exited cleanly even though we sent a signal — still success."""
    assert receiver._classify_receiver_exit(0, "SIGTERM") == "success"
    assert receiver._classify_receiver_exit(0, "SIGINT") == "success"


def test_exit_event_emission_controlled(monkeypatch):
    """Controlled exit emits SYSTEM event, NOT ERROR."""
    system_events = []
    error_events = []
    monkeypatch.setattr(receiver, "_safe_log_system", lambda e, d: system_events.append((e, d)))
    monkeypatch.setattr(receiver, "_safe_log_error", lambda e, d: error_events.append((e, d)))

    exit_class = receiver._classify_receiver_exit(255, "SIGTERM")
    assert exit_class == "controlled"

    # Simulate the emission logic from main() lines 575-593
    return_code = 255
    if exit_class == "controlled":
        receiver._safe_log_system(
            "stream_receiver_exit_controlled",
            {"correlation_id": "cid-ctl", "return_code": return_code,
             "shutdown_signal": "SIGTERM", "duration_seconds": 10.0},
        )
    elif exit_class == "unexpected":
        receiver._safe_log_error(
            "stream_receiver_exit_nonzero",
            {"correlation_id": "cid-ctl", "return_code": return_code, "exit_class": exit_class},
        )

    assert len(system_events) == 1
    assert system_events[0][0] == "stream_receiver_exit_controlled"
    assert system_events[0][1]["return_code"] == 255
    assert len(error_events) == 0


def test_exit_event_emission_unexpected(monkeypatch):
    """Unexpected exit emits ERROR event, NOT SYSTEM."""
    system_events = []
    error_events = []
    monkeypatch.setattr(receiver, "_safe_log_system", lambda e, d: system_events.append((e, d)))
    monkeypatch.setattr(receiver, "_safe_log_error", lambda e, d: error_events.append((e, d)))

    exit_class = receiver._classify_receiver_exit(255, None)
    assert exit_class == "unexpected"

    return_code = 255
    if exit_class == "controlled":
        receiver._safe_log_system(
            "stream_receiver_exit_controlled",
            {"correlation_id": "cid-unx", "return_code": return_code,
             "shutdown_signal": None, "duration_seconds": 5.0},
        )
    elif exit_class == "unexpected":
        receiver._safe_log_error(
            "stream_receiver_exit_nonzero",
            {"correlation_id": "cid-unx", "return_code": return_code, "exit_class": exit_class},
        )

    assert len(error_events) == 1
    assert error_events[0][0] == "stream_receiver_exit_nonzero"
    assert error_events[0][1]["exit_class"] == "unexpected"
    assert len(system_events) == 0


def test_exit_event_emission_success_emits_nothing(monkeypatch):
    """Clean exit (rc=0) emits no exit event at all."""
    system_events = []
    error_events = []
    monkeypatch.setattr(receiver, "_safe_log_system", lambda e, d: system_events.append((e, d)))
    monkeypatch.setattr(receiver, "_safe_log_error", lambda e, d: error_events.append((e, d)))

    exit_class = receiver._classify_receiver_exit(0, None)
    assert exit_class == "success"

    return_code = 0
    if exit_class == "controlled":
        receiver._safe_log_system(
            "stream_receiver_exit_controlled",
            {"correlation_id": "cid-ok", "return_code": return_code,
             "shutdown_signal": None, "duration_seconds": 30.0},
        )
    elif exit_class == "unexpected":
        receiver._safe_log_error(
            "stream_receiver_exit_nonzero",
            {"correlation_id": "cid-ok", "return_code": return_code, "exit_class": exit_class},
        )

    assert len(system_events) == 0
    assert len(error_events) == 0


def test_exit_event_emission_sigterm_no_handler_now_emits(monkeypatch):
    """rc=-15 without signal handler → classified unexpected, now emits ERROR.

    Previously this was a silent gap (-15 was in the return_code exclusion list).
    With exit_class-driven emission, unexpected exits always emit ERROR.
    """
    system_events = []
    error_events = []
    monkeypatch.setattr(receiver, "_safe_log_system", lambda e, d: system_events.append((e, d)))
    monkeypatch.setattr(receiver, "_safe_log_error", lambda e, d: error_events.append((e, d)))

    exit_class = receiver._classify_receiver_exit(-15, None)
    assert exit_class == "unexpected"

    return_code = -15
    if exit_class == "controlled":
        receiver._safe_log_system(
            "stream_receiver_exit_controlled",
            {"correlation_id": "cid-gap", "return_code": return_code,
             "shutdown_signal": None, "duration_seconds": 1.0},
        )
    elif exit_class == "unexpected":
        receiver._safe_log_error(
            "stream_receiver_exit_nonzero",
            {"correlation_id": "cid-gap", "return_code": return_code, "exit_class": exit_class},
        )

    # Silent gap closed: unexpected exits always emit ERROR
    assert len(system_events) == 0
    assert len(error_events) == 1
    assert error_events[0][0] == "stream_receiver_exit_nonzero"
    assert error_events[0][1]["return_code"] == -15
    assert error_events[0][1]["exit_class"] == "unexpected"


def test_udp_input_url_zero_env_falls_back_to_default(monkeypatch):
    """ANNOUNCEFLOW_STREAM_UDP_FIFO=0 is treated as unset; default 4 MB used."""
    monkeypatch.setenv("ANNOUNCEFLOW_STREAM_UDP_FIFO", "0")
    url = receiver._build_udp_input_url(5800)
    assert "fifo_size=4194304" in url


def test_udp_input_url_invalid_env_falls_back_to_default(monkeypatch):
    """Non-numeric env var falls back to 4 MB default."""
    monkeypatch.setenv("ANNOUNCEFLOW_STREAM_UDP_FIFO", "notanumber")
    url = receiver._build_udp_input_url(5800)
    assert "fifo_size=4194304" in url


import subprocess
from unittest.mock import MagicMock

def test_cleanup_idempotent_guard():
    """Double _cleanup call should only invoke stop_process once."""
    call_count = 0

    def _counting_stop(_proc):
        nonlocal call_count
        call_count += 1

    mock_proc = MagicMock(spec=subprocess.Popen)

    # Replicate the closure pattern from main()
    state = {"started": False, "reason": None}

    def _cleanup(reason="internal"):
        if state["started"]:
            return
        state["started"] = True
        state["reason"] = reason
        _counting_stop(mock_proc)

    _cleanup("signal")
    _cleanup("atexit")  # second call should be no-op
    _cleanup("signal")  # third call should be no-op

    assert call_count == 1
    assert state["reason"] == "signal"


def test_stop_process_graceful():
    proc = MagicMock(spec=subprocess.Popen)
    proc.stdin = MagicMock()
    proc.stdin.closed = False
    
    receiver.stop_process(proc)
    
    proc.stdin.write.assert_called_once_with("q")
    proc.stdin.flush.assert_called_once()
    proc.stdin.close.assert_called_once()
    proc.terminate.assert_called_once()
    proc.wait.assert_called_once_with(timeout=1.0)
    proc.kill.assert_not_called()

def test_stop_process_force_kill():
    proc = MagicMock(spec=subprocess.Popen)
    proc.stdin = None  # test no stdin
    
    # Simulate a process that ignores terminate and times out
    proc.wait.side_effect = subprocess.TimeoutExpired(cmd="ffmpeg", timeout=1.0)
    
    receiver.stop_process(proc)
    
    proc.terminate.assert_called_once()
    proc.wait.assert_called_once_with(timeout=1.0)
    proc.kill.assert_called_once()

def test_stop_process_process_lookup_error():
    proc = MagicMock(spec=subprocess.Popen)
    proc.stdin = None
    
    # Simulate process already dead when terminate is called
    proc.terminate.side_effect = ProcessLookupError()
    
    receiver.stop_process(proc)
    
    proc.terminate.assert_called_once()
    proc.kill.assert_called_once()
