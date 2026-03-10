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
    assert url == "udp://0.0.0.0:5800?overrun_nonfatal=1"


def test_udp_input_url_with_fifo(monkeypatch):
    monkeypatch.setenv("ANNOUNCEFLOW_STREAM_UDP_FIFO", "262144")
    url = receiver._build_udp_input_url(5800)
    assert "overrun_nonfatal=1" in url
    assert "fifo_size=262144" in url


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


import subprocess
from unittest.mock import MagicMock

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
