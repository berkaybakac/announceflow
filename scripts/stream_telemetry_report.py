#!/usr/bin/env python3
"""Summarize stream telemetry from events.jsonl.

Focuses on receiver startup/output timing and UDP overrun counters per correlation_id.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from typing import Dict, Optional


DEFAULT_EVENTS_FILE = os.path.join("logs", "events.jsonl")


def _parse_ts(raw: Optional[str]) -> Optional[datetime]:
    text = str(raw or "").strip()
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
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _ms_between(a: Optional[datetime], b: Optional[datetime]) -> Optional[float]:
    if a is None or b is None:
        return None
    return round((b - a).total_seconds() * 1000.0, 1)


def _fmt_ms(value: Optional[float]) -> str:
    if value is None:
        return "-"
    return f"{value:.1f}"


def _iter_jsonl(path: str):
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(obj, dict):
                yield obj


def main() -> int:
    parser = argparse.ArgumentParser(description="Stream telemetry report")
    parser.add_argument("--file", default=DEFAULT_EVENTS_FILE, help="events.jsonl path")
    parser.add_argument("--since", default="", help="ISO lower bound")
    parser.add_argument("--until", default="", help="ISO upper bound")
    parser.add_argument("--limit", type=int, default=0, help="max rows (0=all)")
    args = parser.parse_args()

    if not os.path.isfile(args.file):
        print(f"ERROR: file not found: {args.file}", file=sys.stderr)
        return 2

    since_dt = _parse_ts(args.since) if args.since else None
    until_dt = _parse_ts(args.until) if args.until else None

    rows: Dict[str, dict] = {}

    for item in _iter_jsonl(args.file):
        ts = _parse_ts(item.get("ts"))
        if since_dt and (ts is None or ts < since_dt):
            continue
        if until_dt and (ts is None or ts > until_dt):
            continue

        event = str(item.get("event", "")).strip()
        data = item.get("data") or {}
        if not isinstance(data, dict):
            continue

        cid = str(data.get("correlation_id") or "").strip()
        if not cid:
            continue

        row = rows.setdefault(
            cid,
            {
                "correlation_id": cid,
                "stream_started_ts": None,
                "receiver_started_ts": None,
                "receiver_summary_ts": None,
                "first_input_at": None,
                "first_output_at": None,
                "udp_overrun": 0,
                "demux_errors": 0,
                "immediate_exit": 0,
                "duration_seconds": None,
                "return_code": None,
            },
        )

        if event == "stream_started" and ts is not None:
            row["stream_started_ts"] = ts
        elif event == "stream_receiver_started":
            if ts is not None:
                row["receiver_started_ts"] = ts
        elif event == "stream_receiver_summary":
            if ts is not None:
                row["receiver_summary_ts"] = ts
            row["first_input_at"] = _parse_ts(data.get("first_input_at"))
            row["first_output_at"] = _parse_ts(data.get("first_output_at"))
            row["udp_overrun"] = int(data.get("udp_overrun") or 0)
            row["demux_errors"] = int(data.get("demux_errors") or 0)
            row["immediate_exit"] = int(data.get("immediate_exit") or 0)
            duration = data.get("duration_seconds")
            row["duration_seconds"] = float(duration) if duration is not None else None
            row["return_code"] = data.get("return_code")
        elif event == "stream_receiver_udp_overrun":
            row["udp_overrun"] = max(row["udp_overrun"], int(data.get("overrun_count") or 0))

    ordered = sorted(
        rows.values(),
        key=lambda r: (
            r.get("stream_started_ts")
            or r.get("receiver_started_ts")
            or r.get("receiver_summary_ts")
            or datetime.min.replace(tzinfo=timezone.utc)
        ),
        reverse=True,
    )

    if args.limit > 0:
        ordered = ordered[: args.limit]

    print("correlation_id | start->rx_start_ms | rx_start->first_input_ms | first_input->first_output_ms | overruns | demux_err | imm_exit | duration_s | rc")
    print("-" * 150)

    for r in ordered:
        a = _ms_between(r.get("stream_started_ts"), r.get("receiver_started_ts"))
        b = _ms_between(r.get("receiver_started_ts"), r.get("first_input_at"))
        c = _ms_between(r.get("first_input_at"), r.get("first_output_at"))
        duration_s = r.get("duration_seconds")
        duration_text = "-" if duration_s is None else f"{duration_s:.3f}"
        print(
            f"{r['correlation_id']} | {_fmt_ms(a)} | {_fmt_ms(b)} | {_fmt_ms(c)} | "
            f"{int(r.get('udp_overrun') or 0)} | {int(r.get('demux_errors') or 0)} | "
            f"{int(r.get('immediate_exit') or 0)} | {duration_text} | {r.get('return_code')}"
        )

    print("\nrows=", len(ordered))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
