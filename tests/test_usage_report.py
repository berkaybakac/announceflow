"""scripts/usage_report.py against a synthetic dump in real field formats.

Line formats are copied from real v2.3.5 / v2.4.0 dumps: app log in naive
local time before v2.4.0 and UTC with a trailing Z after, ffmpeg receiver log
always in local time, events in UTC.
"""
import importlib.util
import json
import sqlite3
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("usage_report_under_test", ROOT / "scripts" / "usage_report.py")
usage_report = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(usage_report)

TZ = ZoneInfo("Europe/Istanbul")


def _summary(ts: str, cid: str, duration: float, xrun: int, udp: int = 0) -> str:
    return (f"{ts} [receiver] summary correlation_id={cid} return_code=-9 "
            f"duration_seconds={duration} exit_class=controlled shutdown_signal=SIGTERM "
            f"udp_overrun={udp} alsa_xrun={xrun} xrun_peak_1s={xrun}\n")


@pytest.fixture
def dump(tmp_path):
    d = tmp_path / "site"
    (d / "logs").mkdir(parents=True)

    (d / "logs" / "stream_receiver_ffmpeg.log").write_text(
        "2026-09-01 09:00:00.100 [receiver] correlation_id=a resolved_alsa_device=plughw:1,0 port=5800\n"
        "2026-09-01 09:00:01.000 [alsa @ 0x1] ALSA buffer xrun.\n"
        + _summary("2026-09-01 11:00:00.000", "a", 7200.0, 1)      # 2 h, clean
        + _summary("2026-09-02 10:30:00.000", "b", 1800.0, 30)     # 30 min, clicks
        + _summary("2026-09-02 12:00:30.000", "c", 30.0, 0)        # blip, not "real"
    )
    (d / "logs" / "stream_receiver_ffmpeg.log.1").write_text(
        _summary("2026-08-20 10:00:00.000", "old", 3600.0, 0)     # outside --since
    )
    (d / "announceflow.log").write_text(
        # v2.3.5: naive local time
        "2026-09-01 09:00:05.483 - INFO - [scheduler] [source] recurring play -> anons.mp3 (schedule_id=1)\n"
        "2026-09-01 10:59:59.000 - WARNING - [services.stream_service] StreamService: heartbeat expired (device=x, cid=a)\n"
        "2026-09-01 12:00:00.000 - INFO - [player] Playing next track: 1/2\n"
        "2026-09-01 12:03:00.000 - INFO - [player] Playing next track: 2/2\n"
        "2026-09-01 12:05:00.000 - INFO - [routes.player_routes] [source] manual play -> song.mp3 (media_id=1)\n"
        "2026-09-01 12:06:00.000 - WARNING - [web_panel] Login failed for username='admin' remote_addr=1.2.3.4\n"
        # v2.4.0+: UTC with Z — 06:00Z is 09:00 local on 2026-09-03
        "2026-09-03 06:00:05.000Z - INFO - [scheduler] [source] one-time play -> anons.mp3 (schedule_id=2)\n"
        "2026-09-03 06:10:00.000Z - INFO - [routes.player_routes] [source] manual play -> anons.mp3 (media_id=2)\n"
        "2026-09-03 06:20:00.000Z - INFO - [root] Boot tamamlandı (2049 ms)\n"
    )
    events = [
        {"ts": "2026-09-02T07:00:00.000Z", "event": "login", "data": {"username": "admin"}},
        {"ts": "2026-09-02T21:00:00.000Z", "event": "playlist_daily_summary",
         "data": {"date": "2026-09-01", "play_hours": 1.5, "tracks_played": 30}},
        {"ts": "2026-09-02T21:00:00.000Z", "event": "announcement_queue_health", "data": {}},
    ]
    (d / "logs" / "events.jsonl").write_text("".join(json.dumps(e) + "\n" for e in events) + "not json\n")

    conn = sqlite3.connect(d / "announceflow.db")
    conn.execute("CREATE TABLE media_files (id INTEGER PRIMARY KEY, filename TEXT, media_type TEXT)")
    conn.executemany("INSERT INTO media_files (filename, media_type) VALUES (?, ?)",
                     [("song.mp3", "music"), ("anons.mp3", "announcement")])
    conn.commit()
    conn.close()
    return d


def _report(dump, since="2026-09-01", until="2026-09-30"):
    return usage_report.build_report(
        str(dump),
        datetime.fromisoformat(since),
        datetime.combine(datetime.fromisoformat(until).date(), datetime.max.time()),
        TZ,
    )


def test_stream_metrics(dump):
    s = _report(dump)["stream"]
    assert s["sessions"] == 3                 # Aug 20 session is before --since
    assert s["sessions_10min_plus"] == 2
    assert s["days"] == 2
    assert s["hours"] == round((7200 + 1800 + 30) / 3600, 1)
    assert s["avg_session_minutes"] == 75.0   # (120 + 30) / 2
    assert s["clean_session_pct"] == 50       # 1 of 2 real sessions <= 2 xruns
    assert s["xrun_per_hour"] == round(31 / 2.5, 1)
    assert s["ended_by_heartbeat_loss"] == 1


def test_announcements_use_media_type_and_both_time_formats(dump):
    r = _report(dump)
    a = r["announcements"]
    assert a["scheduled_played"] == 2         # recurring (local) + one-time (UTC)
    assert a["manual_played"] == 1            # song.mp3 is music, not counted
    assert a["days"] == 2                     # 09-01 and 09-03 (UTC → local)


def test_music_panel_and_boots(dump):
    r = _report(dump)
    assert r["music"]["playlist_tracks"] == 2
    assert r["music"]["days"] == 1
    assert r["music"]["hours_from_daily_summaries"] == 1.5
    assert r["panel"] == {"logins": 1, "failed_logins": 1}
    assert r["boots"] == 1
    assert r["active_days"] == 3              # 09-01, 09-02, 09-03


def test_period_filter_excludes_everything_outside(dump):
    r = _report(dump, since="2026-10-01", until="2026-10-31")
    assert r["stream"]["sessions"] == 0
    assert r["announcements"]["scheduled_played"] == 0
    assert r["active_days"] == 0


def test_markdown_renders_multiple_devices_with_fleet_total(dump):
    r = _report(dump)
    text = usage_report.render_markdown([r, r])
    assert text.count("## site") == 2
    assert "## Fleet total" in text
    assert "Clean sessions: 50%" in text


def test_missing_sources_do_not_crash(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    r = _report(empty)
    assert r["stream"]["sessions"] == 0
    assert r["stream"]["clean_session_pct"] is None
    assert r["period"]["days_with_data"] is None
