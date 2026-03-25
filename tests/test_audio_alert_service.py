"""Unit tests for audio alert evaluation service."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from services.audio_alert_service import get_audio_alerts


def _iso_utc(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _event(ts: datetime, event: str, data=None, cat: str = "ERROR") -> dict:
    payload = {
        "ts": _iso_utc(ts),
        "cat": cat,
        "event": event,
    }
    if data is not None:
        payload["data"] = data
    return payload


def _write_jsonl(path: Path, rows) -> None:
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            if isinstance(row, str):
                f.write(row.rstrip("\n"))
                f.write("\n")
            else:
                f.write(json.dumps(row, ensure_ascii=False))
                f.write("\n")


def test_audio_alerts_ok_when_no_tracked_errors(tmp_path: Path):
    now = datetime(2026, 3, 25, 12, 0, tzinfo=timezone.utc)
    events_file = tmp_path / "events.jsonl"
    _write_jsonl(
        events_file,
        [
            _event(now - timedelta(minutes=1), "stream_started", cat="SYSTEM"),
            _event(now - timedelta(minutes=1), "stream_receiver_summary", cat="SYSTEM"),
        ],
    )

    result = get_audio_alerts(window_minutes=10, events_file=str(events_file), now_utc=now)

    assert result["level"] == "ok"
    assert result["reasons"] == []
    assert result["last_event_ts"] is None
    assert result["counts"]["stream_receiver_alsa_xrun"] == 0
    assert result["counts"]["stream_receiver_udp_overrun"] == 0


def test_audio_alerts_warn_when_xrun_threshold_reached(tmp_path: Path):
    now = datetime(2026, 3, 25, 12, 0, tzinfo=timezone.utc)
    events_file = tmp_path / "events.jsonl"
    _write_jsonl(
        events_file,
        [
            _event(
                now - timedelta(minutes=2),
                "stream_receiver_alsa_xrun",
                {"xrun_count": 2},
            ),
            _event(
                now - timedelta(minutes=1),
                "stream_receiver_alsa_xrun",
                {"xrun_count": 1},
            ),
        ],
    )

    result = get_audio_alerts(window_minutes=10, events_file=str(events_file), now_utc=now)

    assert result["level"] == "warn"
    assert any("ALSA XRUN" in reason for reason in result["reasons"])
    assert result["counts"]["stream_receiver_alsa_xrun"] == 3
    assert result["last_event_ts"] == _iso_utc(now - timedelta(minutes=1))


def test_audio_alerts_critical_has_priority_over_warn(tmp_path: Path):
    now = datetime(2026, 3, 25, 12, 0, tzinfo=timezone.utc)
    events_file = tmp_path / "events.jsonl"
    _write_jsonl(
        events_file,
        [
            _event(now - timedelta(minutes=2), "stream_receiver_udp_overrun", {"overrun_count": 1}),
            _event(now - timedelta(minutes=1), "stream_receiver_exit_nonzero", {"return_code": 1}),
        ],
    )

    result = get_audio_alerts(window_minutes=10, events_file=str(events_file), now_utc=now)

    assert result["level"] == "critical"
    assert any("beklenmedik hata" in reason for reason in result["reasons"])
    assert result["counts"]["stream_receiver_udp_overrun"] == 1


def test_audio_alerts_ignore_events_outside_window(tmp_path: Path):
    now = datetime(2026, 3, 25, 12, 0, tzinfo=timezone.utc)
    events_file = tmp_path / "events.jsonl"
    _write_jsonl(
        events_file,
        [
            _event(now - timedelta(minutes=15), "stream_receiver_exit_nonzero", {"return_code": 1}),
            _event(now - timedelta(minutes=20), "stream_receiver_alsa_xrun", {"xrun_count": 5}),
        ],
    )

    result = get_audio_alerts(window_minutes=10, events_file=str(events_file), now_utc=now)

    assert result["level"] == "ok"
    assert result["reasons"] == []
    assert result["last_event_ts"] is None


def test_audio_alerts_skip_malformed_json_lines(tmp_path: Path):
    now = datetime(2026, 3, 25, 12, 0, tzinfo=timezone.utc)
    events_file = tmp_path / "events.jsonl"
    _write_jsonl(
        events_file,
        [
            "{not-json",
            _event(now - timedelta(minutes=1), "stream_receiver_died", {"reason": "receiver_died"}),
        ],
    )

    result = get_audio_alerts(window_minutes=10, events_file=str(events_file), now_utc=now)

    assert result["level"] == "critical"
    assert any("beklenmedik şekilde durdu" in reason for reason in result["reasons"])
    assert result["counts"]["stream_receiver_died"] == 1

