"""incident_report.py merges events.jsonl + announceflow.log + usage_sessions
into one chronological window. Presents existing evidence only."""
from __future__ import annotations

import importlib.util
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load():
    spec = importlib.util.spec_from_file_location(
        "incident_report_under_test", ROOT / "scripts" / "incident_report.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ir = _load()

_AROUND = datetime(2026, 8, 20, 12, 43, 35, tzinfo=timezone.utc)


def _write_events(path: Path, entries: list[dict]) -> None:
    path.write_text("\n".join(json.dumps(e) for e in entries) + "\n")


def _make_usage_db(path: Path, rows: list[tuple]) -> None:
    conn = sqlite3.connect(path)
    conn.execute(
        """
        CREATE TABLE usage_sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            kind TEXT, started_at TIMESTAMP, ended_at TIMESTAMP,
            duration_seconds REAL, source TEXT, detail_json TEXT,
            ended_reason TEXT, gap_seconds REAL
        )
        """
    )
    conn.executemany(
        "INSERT INTO usage_sessions "
        "(kind, started_at, ended_at, duration_seconds, source, ended_reason, gap_seconds) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        rows,
    )
    conn.commit()
    conn.close()


# --- timestamp parsing -------------------------------------------------

def test_parse_app_log_timestamp_utc_post_fix():
    line = "2026-09-02 16:05:02.843Z - INFO - [x] test"
    ts = ir._parse_app_log_timestamp(line)
    assert ts == datetime(2026, 9, 2, 16, 5, 2, 843000, tzinfo=timezone.utc)


def test_parse_app_log_timestamp_local_pre_fix_shifts_minus_3h():
    line = "2026-08-20 15:43:35.865 - WARNING - [web_panel] SLOW_REQUEST"
    ts = ir._parse_app_log_timestamp(line)
    assert ts == datetime(2026, 8, 20, 12, 43, 35, 865000, tzinfo=timezone.utc)


def test_parse_app_log_timestamp_rejects_garbage():
    assert ir._parse_app_log_timestamp("not a log line") is None


# --- events.jsonl --------------------------------------------------------

def test_iter_events_filters_by_window_and_drops_noise(tmp_path):
    events_file = tmp_path / "events.jsonl"
    _write_events(events_file, [
        {"ts": "2026-08-20T12:43:35Z", "cat": "ERROR", "event": "stream_receiver_died", "data": {}},
        {"ts": "2026-08-20T12:43:36Z", "cat": "SCHEDULE", "event": "announcement_queue_health", "data": {}},
        {"ts": "2026-08-20T13:30:00Z", "cat": "ERROR", "event": "out_of_window", "data": {}},
    ])
    since, until = _AROUND - timedelta(minutes=5), _AROUND + timedelta(minutes=5)

    matched = [obj["event"] for _ts, obj in ir._iter_events(str(events_file), since, until)]

    assert matched == ["stream_receiver_died"]


def test_iter_events_reads_rotated_backups(tmp_path):
    base = tmp_path / "events.jsonl"
    _write_events(base, [{"ts": "2026-08-20T12:43:35Z", "cat": "X", "event": "current", "data": {}}])
    _write_events(
        Path(str(base) + ".1"),
        [{"ts": "2026-08-20T12:43:30Z", "cat": "X", "event": "rotated", "data": {}}],
    )
    since, until = _AROUND - timedelta(minutes=1), _AROUND + timedelta(minutes=1)

    matched = {obj["event"] for _ts, obj in ir._iter_events(str(base), since, until)}

    assert matched == {"current", "rotated"}


# --- usage_sessions --------------------------------------------------------

def test_iter_usage_sessions_matches_overlapping_window(tmp_path):
    db_path = tmp_path / "announceflow.db"
    _make_usage_db(db_path, [
        ("stream", "2026-08-20T10:28:10+00:00", "2026-08-20T12:43:35+00:00",
         8126.99, "agent-1", "receiver_died", None),
        ("music", "2026-08-20T09:00:00+00:00", "2026-08-20T09:05:00+00:00",
         300.0, "song.mp3", "stopped", None),
    ])
    since, until = _AROUND - timedelta(minutes=5), _AROUND + timedelta(minutes=5)

    rows = list(ir._iter_usage_sessions(str(db_path), since, until))

    assert len(rows) == 1
    assert rows[0][1]["source"] == "agent-1"


def test_iter_usage_sessions_missing_db_is_silent(tmp_path):
    rows = list(ir._iter_usage_sessions(str(tmp_path / "nope.db"), _AROUND, _AROUND))
    assert rows == []


def test_iter_usage_sessions_missing_table_is_silent(tmp_path):
    db_path = tmp_path / "empty.db"
    sqlite3.connect(db_path).close()
    rows = list(ir._iter_usage_sessions(str(db_path), _AROUND, _AROUND))
    assert rows == []


# --- full merge ------------------------------------------------------------

def test_build_timeline_merges_all_three_sources_in_order(tmp_path):
    events_file = tmp_path / "events.jsonl"
    _write_events(events_file, [
        {"ts": "2026-08-20T12:43:38Z", "cat": "ERROR", "event": "stream_receiver_died", "data": {}},
    ])
    app_log = tmp_path / "announceflow.log"
    app_log.write_text("2026-08-20 12:43:35.000Z - INFO - [x] receiver summary\n")
    db_path = tmp_path / "announceflow.db"
    _make_usage_db(db_path, [
        ("stream", "2026-08-20T12:40:00+00:00", "2026-08-20T12:43:35+00:00",
         215.0, "agent-1", "receiver_died", None),
    ])

    timeline = ir.build_timeline(
        _AROUND, window_minutes=10,
        events_file=str(events_file), app_log=str(app_log), db_path=str(db_path),
    )

    tags = [tag for _ts, tag, _line in timeline]
    assert tags == ["APPLOG", "USAGE", "EVENT"]  # chronological: 12:43:35, 12:43:35, 12:43:38
    assert [ts for ts, _tag, _line in timeline] == sorted(ts for ts, _tag, _line in timeline)
