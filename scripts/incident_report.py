#!/usr/bin/env python3
"""Merge events.jsonl + announceflow.log + usage_sessions into one
chronological incident timeline around a given moment.

Presents existing evidence only — no new telemetry, no new log fields.

Examples:
  python3 scripts/incident_report.py --around "2026-08-20T12:43:35"
  python3 scripts/incident_report.py --around "2026-08-20T12:43:35" --window-minutes 5
  python3 scripts/incident_report.py --around "2026-08-20T12:43:35" \
      --events-file ~/dump/logs/events.jsonl --app-log ~/dump/announceflow.log --db ~/dump/announceflow.db

Run from the directory that holds logs/, announceflow.log and announceflow.db
(a repo checkout, or a pulled field dump — both use the same relative layout).
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Optional

DEFAULT_EVENTS_FILE = os.path.join("logs", "events.jsonl")
DEFAULT_APP_LOG = "announceflow.log"
DEFAULT_DB = "announceflow.db"

# App log timestamps before the 2026-09-02 UTC-logging fix (commit dbefe5d)
# have no trailing "Z" and are local time. This deployment is Europe/Istanbul,
# fixed UTC+3 (Turkey has observed no DST since 2016).
_PRE_FIX_LOCAL_OFFSET = timedelta(hours=3)

# Pure noise, proven during the 2026-09 field analysis: this event accounted
# for 97.7% of event log volume and carries no per-incident signal.
_NOISY_EVENTS = {"announcement_queue_health"}


def _parse_timestamp(raw: str) -> Optional[datetime]:
    text = (raw or "").strip()
    if not text:
        return None
    if " " in text and "T" not in text:
        text = text.replace(" ", "T")
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _resolve_rotated_files(path: str) -> list[str]:
    return sorted(glob.glob(path + "*"))


def _iter_events(path: str, since: datetime, until: datetime):
    for log_file in _resolve_rotated_files(path):
        with open(log_file, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(obj, dict):
                    continue
                ts = _parse_timestamp(str(obj.get("ts", "")))
                if not ts or not (since <= ts <= until):
                    continue
                if obj.get("event") in _NOISY_EVENTS:
                    continue
                yield ts, obj


def _parse_app_log_timestamp(line: str) -> Optional[datetime]:
    # "YYYY-MM-DD HH:MM:SS.fff" (+ "Z" for UTC, post-fix) is the first 23-24 chars.
    prefix = line[:24]
    is_utc = prefix.rstrip().endswith("Z")
    stamp = prefix.rstrip().rstrip("Z")
    try:
        dt = datetime.strptime(stamp, "%Y-%m-%d %H:%M:%S.%f")
    except ValueError:
        return None
    dt = dt.replace(tzinfo=timezone.utc)
    if not is_utc:
        dt = dt - _PRE_FIX_LOCAL_OFFSET
    return dt


def _iter_app_log(path: str, since: datetime, until: datetime):
    for log_file in _resolve_rotated_files(path):
        with open(log_file, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.rstrip("\n")
                if not line.strip():
                    continue
                ts = _parse_app_log_timestamp(line)
                if not ts or not (since <= ts <= until):
                    continue
                yield ts, line


def _iter_usage_sessions(db_path: str, since: datetime, until: datetime):
    if not os.path.isfile(db_path):
        return
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT kind, started_at, ended_at, duration_seconds, source,
                   ended_reason, gap_seconds
            FROM usage_sessions
            WHERE started_at <= ? AND ended_at >= ?
            ORDER BY started_at
            """,
            (until.isoformat(), since.isoformat()),
        ).fetchall()
    except sqlite3.OperationalError:
        return
    finally:
        conn.close()
    for row in rows:
        ts = _parse_timestamp(row["ended_at"]) or _parse_timestamp(row["started_at"])
        if ts is None:
            continue
        yield ts, dict(row)


def build_timeline(
    around: datetime,
    window_minutes: int,
    events_file: str,
    app_log: str,
    db_path: str,
) -> list[tuple[datetime, str, str]]:
    """Returns (timestamp, source_tag, rendered_line) sorted chronologically."""
    since = around - timedelta(minutes=window_minutes)
    until = around + timedelta(minutes=window_minutes)

    timeline: list[tuple[datetime, str, str]] = []

    for ts, obj in _iter_events(events_file, since, until):
        data = json.dumps(obj.get("data", {}), ensure_ascii=False)
        line = f"{obj.get('cat', '?')}/{obj.get('event', '?')}  {data}"
        timeline.append((ts, "EVENT", line))

    for ts, line in _iter_app_log(app_log, since, until):
        timeline.append((ts, "APPLOG", line))

    for ts, row in _iter_usage_sessions(db_path, since, until):
        line = (
            f"{row['kind']} {row['started_at']} -> {row['ended_at']}  "
            f"duration={row['duration_seconds']}s reason={row['ended_reason']} "
            f"gap={row['gap_seconds']} source={row['source']}"
        )
        timeline.append((ts, "USAGE", line))

    timeline.sort(key=lambda item: item[0])
    return timeline


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--around", required=True, help="Incident timestamp (ISO, assumed UTC if no offset)")
    parser.add_argument("--window-minutes", type=int, default=15, help="Minutes before/after --around (default: 15)")
    parser.add_argument("--events-file", default=DEFAULT_EVENTS_FILE)
    parser.add_argument("--app-log", default=DEFAULT_APP_LOG)
    parser.add_argument("--db", default=DEFAULT_DB)
    args = parser.parse_args()

    around = _parse_timestamp(args.around)
    if around is None:
        print(f"ERROR: could not parse --around value: {args.around!r}")
        return 2

    timeline = build_timeline(
        around, args.window_minutes, args.events_file, args.app_log, args.db
    )

    print(f"=== Incident timeline: {around.isoformat()} +/- {args.window_minutes}m ===")
    if not timeline:
        print("(no matching entries in events log, app log, or usage_sessions)")
        return 0
    for ts, tag, line in timeline:
        print(f"{ts.isoformat()}  [{tag:6s}]  {line}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
