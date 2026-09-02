"""diagnose.py must be able to analyze a pulled field dump, not just the
local device log — via --file (explicit path) or --dir (dump directory)."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import diagnose


def _iso(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds").replace("+00:00", "Z")


def test_resolve_log_file_prefers_explicit_file():
    result = diagnose.resolve_log_file(file="/some/explicit.jsonl", dump_dir="/some/dir")
    assert result == "/some/explicit.jsonl"


def test_resolve_log_file_uses_dir_logs_subpath(tmp_path):
    (tmp_path / "logs").mkdir()
    (tmp_path / "logs" / "events.jsonl").write_text("")

    result = diagnose.resolve_log_file(dump_dir=str(tmp_path))
    assert result == str(tmp_path / "logs" / "events.jsonl")


def test_resolve_log_file_falls_back_to_dir_root(tmp_path):
    (tmp_path / "events.jsonl").write_text("")  # no logs/ subdir

    result = diagnose.resolve_log_file(dump_dir=str(tmp_path))
    assert result == str(tmp_path / "events.jsonl")


def test_resolve_log_file_defaults_to_device_log():
    assert diagnose.resolve_log_file() == diagnose.LOG_FILE


def test_get_summary_data_reads_a_pulled_dump(tmp_path):
    dump_events = tmp_path / "events.jsonl"
    now = datetime.now(timezone.utc)
    with open(dump_events, "w") as f:
        f.write(json.dumps({"ts": _iso(now), "event": "track_end", "data": {}}) + "\n")

    stats = diagnose.get_summary_data(minutes=60, file=str(dump_events))

    assert stats is not None
    assert stats["tracks_played"] == 1


def test_get_summary_data_still_works_without_file_arg(monkeypatch, tmp_path):
    """Web route caller (routes/player_routes.py) calls get_summary_data(minutes=...)
    with no file arg and must keep hitting the local device log."""
    fake_log = tmp_path / "events.jsonl"
    now = datetime.now(timezone.utc)
    fake_log.write_text(json.dumps({"ts": _iso(now), "event": "track_end", "data": {}}) + "\n")
    monkeypatch.setattr(diagnose, "LOG_FILE", str(fake_log))

    stats = diagnose.get_summary_data(minutes=60)

    assert stats is not None
    assert stats["tracks_played"] == 1
