#!/usr/bin/env python3
"""Query AnnounceFlow events.jsonl without jq.

Examples:
  python3 scripts/events_query.py --since "2026-03-05T20:30:00" --summary
  python3 scripts/events_query.py --since "2026-03-05 20:30:00" --event stream_started
  python3 scripts/events_query.py --contains overrun --limit 50
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from datetime import datetime, timezone
from typing import Iterable, Optional


DEFAULT_EVENTS_FILE = os.path.join("logs", "events.jsonl")


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
        for fmt in (
            "%Y-%m-%dT%H:%M:%S.%f",
            "%Y-%m-%dT%H:%M:%S",
            "%Y-%m-%d",
        ):
            try:
                dt = datetime.strptime(text, fmt)
                break
            except ValueError:
                continue
        else:
            return None

    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _iter_jsonl(path: str) -> Iterable[dict]:
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        for lineno, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                payload["_line"] = lineno
                payload["_raw"] = line
                yield payload


def main() -> int:
    parser = argparse.ArgumentParser(description="Filter/summarize AnnounceFlow events")
    parser.add_argument("--file", default=DEFAULT_EVENTS_FILE, help="events.jsonl path")
    parser.add_argument("--since", default="", help="ISO timestamp lower bound")
    parser.add_argument("--until", default="", help="ISO timestamp upper bound")
    parser.add_argument("--cat", action="append", default=[], help="Category filter (repeatable)")
    parser.add_argument("--event", action="append", default=[], help="Event filter (repeatable)")
    parser.add_argument("--contains", action="append", default=[], help="Case-insensitive text filter")
    parser.add_argument("--summary", action="store_true", help="Print summary counters")
    parser.add_argument("--summary-only", action="store_true", help="Print only summary counters")
    parser.add_argument("--limit", type=int, default=0, help="Max matching rows to print (0=all)")
    args = parser.parse_args()

    path = args.file
    if not os.path.isfile(path):
        print(f"ERROR: file not found: {path}", file=sys.stderr)
        return 2

    since_dt = _parse_timestamp(args.since) if args.since else None
    until_dt = _parse_timestamp(args.until) if args.until else None

    cat_filter = {c.strip() for c in args.cat if c.strip()}
    event_filter = {e.strip() for e in args.event if e.strip()}
    contains_filter = [c.strip().lower() for c in args.contains if c.strip()]

    matched = 0
    total = 0
    category_counts: Counter[str] = Counter()
    event_counts: Counter[str] = Counter()

    for item in _iter_jsonl(path):
        total += 1
        ts_raw = str(item.get("ts", "")).strip()
        ts_dt = _parse_timestamp(ts_raw)

        if since_dt and (ts_dt is None or ts_dt < since_dt):
            continue
        if until_dt and (ts_dt is None or ts_dt > until_dt):
            continue

        cat = str(item.get("cat", "")).strip()
        event = str(item.get("event", "")).strip()

        if cat_filter and cat not in cat_filter:
            continue
        if event_filter and event not in event_filter:
            continue

        raw_lower = str(item.get("_raw", "")).lower()
        if contains_filter and any(token not in raw_lower for token in contains_filter):
            continue

        matched += 1
        category_counts[cat or "(none)"] += 1
        event_counts[event or "(none)"] += 1

        if not args.summary_only:
            print(item.get("_raw", ""))
            if args.limit > 0 and matched >= args.limit:
                break

    if args.summary or args.summary_only:
        print("=== SUMMARY ===")
        print(f"file={path}")
        print(f"total_rows={total}")
        print(f"matched_rows={matched}")
        if since_dt:
            print(f"since={since_dt.isoformat()}")
        if until_dt:
            print(f"until={until_dt.isoformat()}")

        if category_counts:
            print("categories:")
            for key, count in sorted(category_counts.items(), key=lambda kv: (-kv[1], kv[0])):
                print(f"  {key}: {count}")

        if event_counts:
            print("events:")
            for key, count in sorted(event_counts.items(), key=lambda kv: (-kv[1], kv[0])):
                print(f"  {key}: {count}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
