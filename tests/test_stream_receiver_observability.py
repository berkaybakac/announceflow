"""Tests for stream receiver observability helpers."""

import io

import _stream_receiver as receiver


def _new_counters():
    return {
        "udp_overrun": 0,
        "demux_errors": 0,
        "immediate_exit": 0,
        "audio_device_errors": 0,
        "connection_errors": 0,
        "first_input_at": None,
        "first_output_at": None,
        "first_overrun_at": None,
        "last_overrun_at": None,
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
    receiver._process_ffmpeg_line(
        "[in#0/s16le @ 0x2] Error during demuxing: Immediate exit requested",
        buf,
        counters,
    )
    receiver._process_ffmpeg_line("cannot open audio device default", buf, counters)
    receiver._process_ffmpeg_line("connection refused", buf, counters)

    assert counters["udp_overrun"] == 1
    assert counters["demux_errors"] == 1
    assert counters["immediate_exit"] == 1
    assert counters["audio_device_errors"] == 1
    assert counters["connection_errors"] == 1
    assert counters["first_input_at"] is not None
    assert counters["first_output_at"] is not None
    assert counters["first_overrun_at"] is not None
    assert counters["last_overrun_at"] is not None
