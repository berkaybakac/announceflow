"""CLI script tests for stream diagnostics helpers."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _run_script(rel_script: str, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(ROOT / rel_script), *args],
        capture_output=True,
        text=True,
        check=False,
        cwd=str(ROOT),
    )


def test_events_query_summary_only(tmp_path):
    events_file = tmp_path / "events.jsonl"
    rows = [
        {"ts": "2026-03-05T20:29:59Z", "cat": "SYSTEM", "event": "boot"},
        {"ts": "2026-03-05T20:30:01Z", "cat": "SYSTEM", "event": "stream_started"},
        {"ts": "2026-03-05T20:31:00Z", "cat": "ERROR", "event": "stream_receiver_udp_overrun"},
    ]
    events_file.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")

    proc = _run_script(
        "scripts/events_query.py",
        "--file",
        str(events_file),
        "--since",
        "2026-03-05T20:30:00",
        "--summary-only",
    )
    assert proc.returncode == 0
    assert "matched_rows=2" in proc.stdout
    assert "SYSTEM: 1" in proc.stdout
    assert "ERROR: 1" in proc.stdout


def test_ffmpeg_overrun_count_respects_since_filter_and_repeat_lines(tmp_path):
    ffmpeg_log = tmp_path / "stream_receiver_ffmpeg.log"
    ffmpeg_log.write_text(
        "\n".join(
            [
                "2026-03-05 20:29:59.000 [udp @ 0x1] Circular buffer overrun. Surviving due to overrun_nonfatal option",
                "2026-03-05 20:30:01.000 [udp @ 0x1] Circular buffer overrun. Surviving due to overrun_nonfatal option",
                "2026-03-05 20:30:02.000     Last message repeated 4 times",
                "[udp @ 0x2] Circular buffer overrun. Surviving due to overrun_nonfatal option",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    proc = _run_script(
        "scripts/ffmpeg_overrun_count.py",
        "--file",
        str(ffmpeg_log),
        "--since",
        "2026-03-05 20:30:00",
    )
    assert proc.returncode == 0
    assert "direct_overrun_lines=1" in proc.stdout
    assert "repeated_overrun_lines=4" in proc.stdout
    assert "overrun_total=5" in proc.stdout


def test_stream_telemetry_report_prints_latency_columns(tmp_path):
    events_file = tmp_path / "events.jsonl"
    rows = [
        {"ts": "2026-03-05T20:30:00.000Z", "cat": "SYSTEM", "event": "stream_started", "data": {"correlation_id": "cid-1"}},
        {"ts": "2026-03-05T20:30:00.100Z", "cat": "SYSTEM", "event": "stream_receiver_started", "data": {"correlation_id": "cid-1"}},
        {
            "ts": "2026-03-05T20:30:01.000Z",
            "cat": "SYSTEM",
            "event": "stream_receiver_summary",
            "data": {
                "correlation_id": "cid-1",
                "first_input_at": "2026-03-05T20:30:00.300Z",
                "first_output_at": "2026-03-05T20:30:00.450Z",
                "udp_overrun": 2,
                "alsa_xrun": 3,
                "demux_errors": 1,
                "immediate_exit": 1,
                "duration_seconds": 12.5,
                "return_code": 0,
            },
        },
    ]
    events_file.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")

    proc = _run_script(
        "scripts/stream_telemetry_report.py",
        "--file",
        str(events_file),
        "--since",
        "2026-03-05T20:30:00",
    )
    assert proc.returncode == 0
    assert "cid-1 | 100.0 | 200.0 | 150.0 | 2 | 3 | 1 | 1 | 12.500 | 0" in proc.stdout
    assert "rows= 1" in proc.stdout


def test_stream_telemetry_report_uses_first_input_output_events_without_summary(tmp_path):
    events_file = tmp_path / "events.jsonl"
    rows = [
        {"ts": "2026-03-05T20:30:00.000Z", "cat": "SYSTEM", "event": "stream_started", "data": {"correlation_id": "cid-2"}},
        {"ts": "2026-03-05T20:30:00.100Z", "cat": "SYSTEM", "event": "stream_receiver_started", "data": {"correlation_id": "cid-2"}},
        {"ts": "2026-03-05T20:30:00.300Z", "cat": "SYSTEM", "event": "stream_receiver_first_input", "data": {"correlation_id": "cid-2", "at": "2026-03-05T20:30:00.300Z"}},
        {"ts": "2026-03-05T20:30:00.450Z", "cat": "SYSTEM", "event": "stream_receiver_first_output", "data": {"correlation_id": "cid-2", "at": "2026-03-05T20:30:00.450Z"}},
    ]
    events_file.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")

    proc = _run_script(
        "scripts/stream_telemetry_report.py",
        "--file",
        str(events_file),
        "--since",
        "2026-03-05T20:30:00",
    )
    assert proc.returncode == 0
    assert "cid-2 | 100.0 | 200.0 | 150.0 | 0 | 0 | 0 | 0 | - | None" in proc.stdout
    assert "rows= 1" in proc.stdout
