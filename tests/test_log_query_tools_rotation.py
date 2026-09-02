"""events_query.py / stream_telemetry_report.py must also read rotated
event log backups (events.jsonl.1, .2, ...), not just the current file.

diagnose.py already did this; these two tools did not, so a summary run
against a pulled field dump silently missed everything in the rotated files.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load(name: str, rel_path: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


events_query = _load("events_query_under_test", "scripts/events_query.py")
stream_telemetry_report = _load(
    "stream_telemetry_report_under_test", "scripts/stream_telemetry_report.py"
)


def _write_line(path: Path, event: str) -> None:
    path.write_text(json.dumps({"ts": "2026-08-20T12:00:00Z", "event": event}) + "\n")


def test_events_query_reads_rotated_backups(tmp_path):
    base = tmp_path / "events.jsonl"
    _write_line(base, "current_file_event")
    _write_line(Path(str(base) + ".1"), "rotated_backup_event")

    items = list(events_query._iter_jsonl(str(base)))
    events = {item["event"] for item in items}

    assert events == {"current_file_event", "rotated_backup_event"}


def test_stream_telemetry_report_reads_rotated_backups(tmp_path):
    base = tmp_path / "events.jsonl"
    _write_line(base, "current_file_event")
    _write_line(Path(str(base) + ".1"), "rotated_backup_event")

    items = list(stream_telemetry_report._iter_jsonl(str(base)))
    events = {item["event"] for item in items}

    assert events == {"current_file_event", "rotated_backup_event"}
