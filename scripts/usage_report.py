#!/usr/bin/env python3
"""Usage report for one or more pulled field dumps.

Answers "how much is this device actually used, and how well does it work"
from evidence that every version already writes, so it also covers devices
still on older releases:

  stream_receiver_ffmpeg.log*  one summary line per live-stream session
                               (duration, ALSA xruns) since v2.2.0
  announceflow.log*            stream start/stop, heartbeat losses, playlist
                               tracks, scheduled/manual plays, boots
  announceflow.db              media_files (to tell announcements from music),
                               usage_sessions (v2.4.0+)
  logs/events.jsonl*           panel logins, daily summaries (short history
                               on pre-v2.4.0 devices: queue-health noise)

Examples:
  python3 scripts/usage_report.py --dir ~/dumps/alparslan
  python3 scripts/usage_report.py --dir ~/dumps/a --dir ~/dumps/b --since 2026-07-01
  python3 scripts/usage_report.py --dir ~/dumps/a --json report.json

Pull a dump first with scripts/pull_dump.sh <host> <dest-dir>.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import sqlite3
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Iterable, Optional
from zoneinfo import ZoneInfo

DEFAULT_TZ = "Europe/Istanbul"
# A session shorter than this is a connect/retry blip, not real use.
REAL_SESSION_SECONDS = 600
# <= this many xruns over a whole session is inaudible in practice.
CLEAN_SESSION_MAX_XRUNS = 2

_APP_TS = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{3})(Z?)")
_KV = re.compile(r"(\w+)=(\S+)")
_SCHEDULED_PLAY = re.compile(r"\[source\] (recurring|one-time) play -> (.+?) \(schedule_id=")
_MANUAL_PLAY = re.compile(r"\[source\] manual play -> (.+?) \(media_id=")
_AGENT_ID = re.compile(r"agent-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


def _rotated(path: str) -> list[str]:
    """Base file plus rotated backups (.1, .2, ...)."""
    return sorted(glob.glob(path + "*"))


def _read_lines(paths: Iterable[str]) -> Iterable[str]:
    for path in paths:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            yield from f


def _app_log_time(line: str, tz: ZoneInfo) -> Optional[datetime]:
    """App log: naive local time before v2.4.0, '...Z' (UTC) since."""
    m = _APP_TS.match(line)
    if not m:
        return None
    dt = datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S.%f")
    if m.group(2):
        return dt.replace(tzinfo=timezone.utc).astimezone(tz).replace(tzinfo=None)
    return dt


def _local_time(line: str) -> Optional[datetime]:
    """ffmpeg receiver log: always the Pi's local time, no suffix."""
    try:
        return datetime.strptime(line[:23], "%Y-%m-%d %H:%M:%S.%f")
    except ValueError:
        return None


def _event_time(raw: str, tz: ZoneInfo) -> Optional[datetime]:
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(tz).replace(tzinfo=None)


def _media_types(db_path: str) -> dict[str, str]:
    if not os.path.exists(db_path):
        return {}
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        return {row[0]: row[1] for row in conn.execute("SELECT filename, media_type FROM media_files")}
    except sqlite3.Error:
        return {}
    finally:
        conn.close()


def _usage_sessions(db_path: str, since: datetime, until: datetime, tz: ZoneInfo) -> dict:
    """usage_sessions (v2.4.0+): hours per kind; empty on older devices."""
    out: dict = {}
    if not os.path.exists(db_path):
        return out
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        rows = conn.execute("SELECT kind, started_at, duration_seconds FROM usage_sessions").fetchall()
    except sqlite3.Error:
        return out
    finally:
        conn.close()
    for kind, started_at, duration in rows:
        started = _event_time(str(started_at or ""), tz)
        if started is None or not (since <= started <= until):
            continue
        slot = out.setdefault(kind, {"sessions": 0, "hours": 0.0})
        slot["sessions"] += 1
        slot["hours"] += float(duration or 0) / 3600.0
    return out


def build_report(dump_dir: str, since: datetime, until: datetime, tz: ZoneInfo) -> dict:
    in_range = lambda t: t is not None and since <= t <= until  # noqa: E731
    media_type = _media_types(os.path.join(dump_dir, "announceflow.db"))
    coverage: dict[str, list] = defaultdict(lambda: [None, None])

    def seen(source: str, t: datetime) -> None:
        lo, hi = coverage[source]
        coverage[source] = [t if lo is None or t < lo else lo, t if hi is None or t > hi else hi]

    active_days: dict[str, set] = defaultdict(set)

    # ── Live stream sessions (ffmpeg receiver summaries) ──────────────────────
    # One stream can span several receiver segments with the same
    # correlation_id (paused for an announcement or prayer, then resumed),
    # so segments are summed per correlation_id.
    segments = 0
    by_cid: dict[str, dict] = {}
    for line in _read_lines(_rotated(os.path.join(dump_dir, "logs", "stream_receiver_ffmpeg.log"))):
        t = _local_time(line)
        if t is None:
            continue
        seen("stream_receiver_ffmpeg.log", t)
        if "[receiver] summary" not in line:
            continue
        kv = dict(_KV.findall(line))
        duration = float(kv.get("duration_seconds", 0) or 0)
        start = t - timedelta(seconds=duration)
        if not in_range(start):
            continue
        segments += 1
        cid = kv.get("correlation_id") or f"segment-{segments}"
        sess = by_cid.setdefault(cid, {"start": start, "duration": 0.0, "xrun": 0, "udp_overrun": 0})
        sess["start"] = min(sess["start"], start)
        sess["duration"] += duration
        sess["xrun"] += int(kv.get("alsa_xrun", 0) or 0)
        sess["udp_overrun"] += int(kv.get("udp_overrun", 0) or 0)
        active_days["stream"].add(start.date())

    sessions = list(by_cid.values())
    real = [s for s in sessions if s["duration"] >= REAL_SESSION_SECONDS]
    real_hours = sum(s["duration"] for s in real) / 3600.0

    # ── App log: heartbeat losses, music tracks, plays, boots ────────────────
    heartbeat_lost = 0
    playlist_tracks = 0
    scheduled_announcements = 0
    manual_announcements = 0
    boots = 0
    failed_logins = 0
    music_paused_for_prayer = 0
    sender_pcs: set[str] = set()
    for line in _read_lines(_rotated(os.path.join(dump_dir, "announceflow.log"))):
        t = _app_log_time(line, tz)
        if t is None:
            continue
        seen("announceflow.log", t)
        if not in_range(t):
            continue
        if "StreamService: heartbeat expired" in line:
            heartbeat_lost += 1
            sender_pcs.update(_AGENT_ID.findall(line))
        elif "Prayer time - saving playlist state" in line:
            music_paused_for_prayer += 1
        elif "[player] Playing next track" in line:
            playlist_tracks += 1
            active_days["music"].add(t.date())
        elif "Boot tamamland" in line:
            boots += 1
        elif "Login failed" in line:
            failed_logins += 1
        else:
            m = _SCHEDULED_PLAY.search(line)
            if m and media_type.get(m.group(2)) == "announcement":
                scheduled_announcements += 1
                active_days["announcement"].add(t.date())
                continue
            m = _MANUAL_PLAY.search(line)
            if m and media_type.get(m.group(1)) == "announcement":
                manual_announcements += 1
                active_days["announcement"].add(t.date())

    # ── Events: panel logins, music hours from daily playlist summaries ──────
    logins = 0
    prayer_silence_windows = 0
    agent_versions: dict[str, tuple[datetime, str]] = {}  # device -> (seen at, version)
    music_hours_by_date: dict[str, float] = {}
    for line in _read_lines(_rotated(os.path.join(dump_dir, "logs", "events.jsonl"))):
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        t = _event_time(str(obj.get("ts", "")), tz)
        if t is None:
            continue
        seen("events.jsonl", t)
        event = obj.get("event")
        data = obj.get("data") or {}
        if in_range(t) and isinstance(data, dict):
            sender_pcs.update(_AGENT_ID.findall(json.dumps(data)))
        if event == "login" and in_range(t):
            logins += 1
        elif event == "stream_agent_version" and in_range(t):
            device = str(data.get("device_id") or "")
            if device and (device not in agent_versions or t >= agent_versions[device][0]):
                agent_versions[device] = (t, str(data.get("agent_version") or "unreported"))
        elif event == "policy_decision" and in_range(t):
            if data.get("policy") == "prayer" and data.get("silence_active"):
                prayer_silence_windows += 1
        elif event == "playlist_daily_summary":
            day = str(data.get("date", ""))
            try:
                day_dt = datetime.strptime(day, "%Y-%m-%d")
            except ValueError:
                continue
            if in_range(day_dt):
                music_hours_by_date[day] = float(data.get("play_hours") or 0.0)

    all_days = set().union(*active_days.values()) if active_days else set()
    days_in_period = None
    covered = [c for c in coverage.values() if c[0] is not None]
    if covered:
        first = max(since, min(c[0] for c in covered))
        last = min(until, max(c[1] for c in covered))
        days_in_period = (last.date() - first.date()).days + 1

    data_since = min((c[0] for c in covered), default=None)

    return {
        "device": os.path.basename(os.path.normpath(dump_dir)),
        "data_since": data_since.date().isoformat() if data_since else None,
        "period": {"since": since.date().isoformat(), "until": until.date().isoformat(),
                   "days_with_data": days_in_period},
        "active_days": len(all_days),
        "stream": {
            "sessions": len(sessions),
            "receiver_segments": segments,
            "sessions_10min_plus": len(real),
            "days": len(active_days["stream"]),
            "hours": round(sum(s["duration"] for s in sessions) / 3600.0, 1),
            "avg_session_minutes": round(real_hours * 60 / len(real), 1) if real else None,
            "clean_session_pct": round(100.0 * sum(1 for s in real if s["xrun"] <= CLEAN_SESSION_MAX_XRUNS) / len(real)) if real else None,
            "xrun_per_hour": round(sum(s["xrun"] for s in real) / real_hours, 1) if real_hours else None,
            "udp_overrun_total": sum(s["udp_overrun"] for s in sessions),
            "ended_by_heartbeat_loss": heartbeat_lost,
            "sender_pcs": len(sender_pcs),
            # Latest EXE build seen per PC ("unreported" = EXE built before version reporting).
            "agent_versions": {device: version for device, (_, version) in sorted(agent_versions.items())},
        },
        "music": {
            "days": len(active_days["music"]),
            "playlist_tracks": playlist_tracks,
            "hours_from_daily_summaries": round(sum(music_hours_by_date.values()), 1),
            "daily_summary_days": len(music_hours_by_date),
        },
        "announcements": {
            "scheduled_played": scheduled_announcements,
            "manual_played": manual_announcements,
            "days": len(active_days["announcement"]),
        },
        "prayer": {
            "music_paused_for_prayer": music_paused_for_prayer,
            "prayer_silence_windows": prayer_silence_windows,
        },
        "panel": {"logins": logins, "failed_logins": failed_logins},
        "boots": boots,
        "usage_sessions_v2_4": _usage_sessions(os.path.join(dump_dir, "announceflow.db"), since, until, tz),
        "coverage": {k: [v[0].date().isoformat(), v[1].date().isoformat()] for k, v in coverage.items()},
    }


def _fmt(value) -> str:
    return "–" if value is None else str(value)


def render_markdown(reports: list[dict]) -> str:
    out = []
    for r in reports:
        s, m, a = r["stream"], r["music"], r["announcements"]
        out.append(f"## {r['device']}  ({r['period']['since']} → {r['period']['until']})")
        out.append("")
        out.append(f"- Active days: **{r['active_days']}** of {_fmt(r['period']['days_with_data'])} days with data"
                   f" (evidence since {_fmt(r['data_since'])})")
        out.append(f"- Live stream: **{s['hours']} h** in {s['sessions']} sessions, {s['sessions_10min_plus']} of them ≥10 min "
                   f"({s['days']} days, avg {_fmt(s['avg_session_minutes'])} min, {s['sender_pcs']} sender PC(s))")
        clean = "–" if s["clean_session_pct"] is None else f"{s['clean_session_pct']}%"
        out.append(f"  - Clean sessions: {clean} · ALSA xrun/h: {_fmt(s['xrun_per_hour'])} · "
                   f"UDP overruns: {s['udp_overrun_total']} · ended by sender loss: {s['ended_by_heartbeat_loss']}")
        if s["agent_versions"]:
            out.append("  - Sender agent versions: " + ", ".join(
                f"{device[:14]}… {version}" for device, version in s["agent_versions"].items()))
        out.append(f"- Music: {m['days']} days, {m['playlist_tracks']} playlist tracks"
                   + (f", {m['hours_from_daily_summaries']} h in {m['daily_summary_days']} summarised days"
                      if m["daily_summary_days"] else ""))
        out.append(f"- Announcements played: {a['scheduled_played']} scheduled, {a['manual_played']} manual ({a['days']} days)")
        p = r["prayer"]
        out.append(f"- Prayer-time automation: music paused {p['music_paused_for_prayer']}×"
                   f" · prayer silence windows (events): {p['prayer_silence_windows']}")
        out.append(f"- Panel logins: {r['panel']['logins']} (failed: {r['panel']['failed_logins']}) · boots: {r['boots']}")
        if r["usage_sessions_v2_4"]:
            parts = ", ".join(f"{k}: {v['sessions']} sessions / {v['hours']:.1f} h"
                              for k, v in sorted(r["usage_sessions_v2_4"].items()))
            out.append(f"- usage_sessions (v2.4+): {parts}")
        out.append("- Data coverage: " + ", ".join(f"{k} {v[0]}→{v[1]}" for k, v in sorted(r["coverage"].items())))
        out.append("")
    if len(reports) > 1:
        out.append("## Fleet total")
        out.append("")
        out.append(f"- Devices: {len(reports)} · stream hours: {round(sum(r['stream']['hours'] for r in reports), 1)}"
                   f" · playlist tracks: {sum(r['music']['playlist_tracks'] for r in reports)}"
                   f" · announcements: {sum(r['announcements']['scheduled_played'] + r['announcements']['manual_played'] for r in reports)}"
                   f" · prayer music pauses: {sum(r['prayer']['music_paused_for_prayer'] for r in reports)}")
        out.append("")
    return "\n".join(out)


def main() -> int:
    parser = argparse.ArgumentParser(description="Usage report from pulled field dumps")
    parser.add_argument("--dir", action="append", required=True, help="dump directory (repeatable)")
    parser.add_argument("--since", default="2000-01-01", help="YYYY-MM-DD, local date (inclusive)")
    parser.add_argument("--until", default="2100-01-01", help="YYYY-MM-DD, local date (inclusive)")
    parser.add_argument("--tz", default=DEFAULT_TZ, help=f"device timezone (default {DEFAULT_TZ})")
    parser.add_argument("--json", help="also write the full report as JSON to this path")
    args = parser.parse_args()

    tz = ZoneInfo(args.tz)
    since = datetime.combine(date.fromisoformat(args.since), datetime.min.time())
    until = datetime.combine(date.fromisoformat(args.until), datetime.max.time())
    reports = [build_report(d, since, until, tz) for d in args.dir]

    print(render_markdown(reports))
    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(reports, f, ensure_ascii=False, indent=2)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
