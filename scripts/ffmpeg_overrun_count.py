#!/usr/bin/env python3
"""Count ffmpeg UDP circular buffer overruns from stream_receiver_ffmpeg.log.

Supports timestamp-prefixed lines in format:
  YYYY-MM-DD HH:MM:SS.mmm <message>
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from datetime import datetime
from typing import Optional


DEFAULT_LOG_FILE = os.path.join("logs", "stream_receiver_ffmpeg.log")
TS_RE = re.compile(r"^(\d{4}-\d{2}-\d{2}) (\d{2}:\d{2}:\d{2}(?:\.\d{3})?)\s+(.*)$")
REPEAT_RE = re.compile(r"Last message repeated\s+(\d+)\s+times", re.IGNORECASE)


def _parse_cli_ts(raw: str) -> Optional[datetime]:
    text = (raw or "").strip()
    if not text:
        return None
    if "T" in text:
        text = text.replace("T", " ")
    for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def _parse_line(line: str):
    m = TS_RE.match(line.rstrip("\n"))
    if not m:
        return None, line
    date_part, time_part, msg = m.groups()
    if "." not in time_part:
        time_part = f"{time_part}.000"
    try:
        ts = datetime.strptime(f"{date_part} {time_part}", "%Y-%m-%d %H:%M:%S.%f")
    except ValueError:
        ts = None
    return ts, msg


def main() -> int:
    p = argparse.ArgumentParser(description="Count ffmpeg circular buffer overruns")
    p.add_argument("--file", default=DEFAULT_LOG_FILE, help="ffmpeg log file")
    p.add_argument("--since", default="", help='lower bound, e.g. "2026-03-05 20:30:00"')
    p.add_argument("--until", default="", help='upper bound, e.g. "2026-03-06 23:59:59"')
    args = p.parse_args()

    path = args.file
    if not os.path.isfile(path):
        print(f"ERROR: file not found: {path}", file=sys.stderr)
        return 2

    since_dt = _parse_cli_ts(args.since) if args.since else None
    until_dt = _parse_cli_ts(args.until) if args.until else None
    if (args.since and since_dt is None) or (args.until and until_dt is None):
        print("ERROR: invalid --since/--until format", file=sys.stderr)
        return 2

    total = 0
    line_hits = 0
    repeated_hits = 0

    with open(path, "r", encoding="utf-8", errors="replace") as f:
        for raw in f:
            ts, msg = _parse_line(raw)
            if since_dt and ts is None:
                continue
            if since_dt and ts < since_dt:
                continue
            if until_dt and ts is None:
                continue
            if until_dt and ts > until_dt:
                continue

            low = msg.lower()
            if "circular buffer overrun" in low:
                total += 1
                line_hits += 1
                continue

            rm = REPEAT_RE.search(msg)
            if rm:
                count = int(rm.group(1))
                total += count
                repeated_hits += count

    print(f"file={path}")
    print(f"since={args.since or '-'}")
    print(f"until={args.until or '-'}")
    print(f"direct_overrun_lines={line_hits}")
    print(f"repeated_overrun_lines={repeated_hits}")
    print(f"overrun_total={total}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
