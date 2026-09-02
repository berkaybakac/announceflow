"""Regression: diagnose.py must never report "SİSTEM MÜKEMMEL" (system
perfect) when a metric family has zero backing events in the log corpus.

Root cause of the bug this guards against: diagnose.py counted xruns from
xrun_snapshot / system_health / stream_jitter_anomaly / sender_ping_latency_high,
none of which exist in v2.2.0 (the field-deployed build, 2026-03-30) — they
were added the next day (2026-04-01). Run against a real v2.2.0 field dump,
diagnose.py reported "SİSTEM MÜKEMMEL" while the dump had a real, unexpected
stream crash. See docs/backlog.md.
"""
from __future__ import annotations

import io
import json
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone

import diagnose


def _iso(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds").replace("+00:00", "Z")


def _write(path, entries: list[dict]) -> None:
    path.write_text("\n".join(json.dumps(e) for e in entries) + "\n")


NOW = datetime.now(timezone.utc)


# --- get_summary_data: "unsupported" detection ---------------------------

def test_pre_2026_04_build_flags_all_four_metrics_unsupported(tmp_path):
    """v2.2.0-shaped log: only stream_receiver_summary/track_end exist."""
    events = tmp_path / "events.jsonl"
    _write(events, [
        {"ts": _iso(NOW), "event": "stream_receiver_summary",
         "data": {"alsa_xrun": 3519, "duration_seconds": 300000}},
    ])

    stats = diagnose.get_summary_data(minutes=100000, file=str(events))

    assert set(stats["unsupported"]) == {"jitters", "ping_warnings", "system_health"}
    assert "xruns" not in stats["unsupported"]  # stream_receiver_summary covers it
    assert stats["xruns"] == 3519


def test_fully_instrumented_build_has_no_unsupported_metrics(tmp_path):
    events = tmp_path / "events.jsonl"
    _write(events, [
        {"ts": _iso(NOW), "event": "system_health", "data": {"temp_c": 45.0}},
        {"ts": _iso(NOW), "event": "stream_receiver_summary", "data": {"alsa_xrun": 0, "duration_seconds": 600}},
        {"ts": _iso(NOW), "event": "stream_jitter_anomaly", "data": {}},
        {"ts": _iso(NOW), "event": "sender_ping_latency_high", "data": {}},
    ])

    stats = diagnose.get_summary_data(minutes=10, file=str(events))

    assert stats["unsupported"] == []


def test_empty_log_flags_everything_unsupported(tmp_path):
    events = tmp_path / "events.jsonl"
    events.write_text("")

    stats = diagnose.get_summary_data(minutes=10, file=str(events))

    assert set(stats["unsupported"]) == {"xruns", "jitters", "ping_warnings", "system_health"}


def test_xrun_counted_from_stream_receiver_summary_not_xrun_snapshot(tmp_path):
    """xrun_snapshot alone (no stream_receiver_summary) must not silently
    count as 0-and-supported nor crash; alsa_xrun from the summary is the
    source of truth."""
    events = tmp_path / "events.jsonl"
    _write(events, [
        {"ts": _iso(NOW), "event": "stream_receiver_summary",
         "data": {"alsa_xrun": 42, "duration_seconds": 3600}},
    ])

    stats = diagnose.get_summary_data(minutes=10, file=str(events))

    assert stats["xruns"] == 42
    assert stats["xrun_hours"] == 1.0


# --- _print_report: never claim "MÜKEMMEL" with a coverage gap -----------

def _render(stats, minutes=60) -> str:
    buf = io.StringIO()
    with redirect_stdout(buf):
        diagnose._print_report(stats, minutes)
    return buf.getvalue()


def test_report_never_says_perfect_with_missing_metrics_even_if_measured_ones_are_clean():
    stats = {
        "xruns": 0, "xrun_hours": 1.0, "jitters": 0, "ping_warnings": 0,
        "temps": [], "cpu_loads": [], "wifi_signals": [],
        "tracks_played": 0, "tracks_skipped": 0, "last_health": None,
        "total_entries": 0, "lookback_minutes": 60,
        "unsupported": ["jitters", "ping_warnings", "system_health"],
    }
    output = _render(stats)

    assert "SİSTEM MÜKEMMEL" not in output
    assert "KISITLI TEŞHİS" in output
    assert "Ölçülebilen metriklerde sorun tespit edilmedi" in output


def test_report_says_perfect_only_when_fully_supported_and_clean():
    stats = {
        "xruns": 0, "xrun_hours": 1.0, "jitters": 0, "ping_warnings": 0,
        "temps": [50.0], "cpu_loads": [0.1], "wifi_signals": [-40],
        "tracks_played": 1, "tracks_skipped": 0, "last_health": {},
        "total_entries": 3, "lookback_minutes": 60,
        "unsupported": [],
    }
    output = _render(stats)

    assert "SİSTEM MÜKEMMEL" in output
    assert "KISITLI TEŞHİS" not in output


def test_report_flags_real_problem_regardless_of_coverage_gaps():
    """The actual field-dump scenario: xruns are measurable and bad, other
    metrics are unsupported. Must show the real problem, not hide it behind
    the coverage caveat."""
    stats = {
        "xruns": 3519, "xrun_hours": 83.33, "jitters": 0, "ping_warnings": 0,
        "temps": [], "cpu_loads": [], "wifi_signals": [],
        "tracks_played": 0, "tracks_skipped": 0, "last_health": None,
        "total_entries": 34, "lookback_minutes": 100000,
        "unsupported": ["jitters", "ping_warnings", "system_health"],
    }
    output = _render(stats)

    assert "SİSTEM MÜKEMMEL" not in output
    assert "sık kesiliyor" in output
    assert "KISITLI TEŞHİS" in output
