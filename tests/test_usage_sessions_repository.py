"""Tests for the usage_sessions table (schema + UsageRepository)."""
from __future__ import annotations

import json
import os
import sqlite3
import tempfile

import pytest

import database as db
from database.media_repository import MediaRepository
from database.schedule_repository import ScheduleRepository
from database.playback_repository import PlaybackRepository
from database.usage_repository import UsageRepository


@pytest.fixture
def isolated_db():
    """Point the db module at a fresh temp file, restore afterwards."""
    tmpdir = tempfile.TemporaryDirectory()
    test_db_path = os.path.join(tmpdir.name, "test.db")
    orig = {
        "DATABASE_PATH": db.DATABASE_PATH,
        "media_repo": db._media_repo,
        "schedule_repo": db._schedule_repo,
        "playback_repo": db._playback_repo,
        "usage_repo": db._usage_repo,
    }
    db.DATABASE_PATH = test_db_path
    db._media_repo = MediaRepository(test_db_path)
    db._schedule_repo = ScheduleRepository(test_db_path)
    db._playback_repo = PlaybackRepository(test_db_path)
    db._usage_repo = UsageRepository(test_db_path)
    db.init_database()

    yield test_db_path

    db.DATABASE_PATH = orig["DATABASE_PATH"]
    db._media_repo = orig["media_repo"]
    db._schedule_repo = orig["schedule_repo"]
    db._playback_repo = orig["playback_repo"]
    db._usage_repo = orig["usage_repo"]
    tmpdir.cleanup()


def test_init_database_creates_usage_sessions_table(isolated_db):
    conn = sqlite3.connect(isolated_db)
    cols = {row[1] for row in conn.execute("PRAGMA table_info(usage_sessions)")}
    conn.close()
    assert cols == {
        "id", "kind", "started_at", "ended_at", "duration_seconds",
        "source", "detail_json", "created_at",
    }


def test_kind_check_constraint_rejects_invalid_value(isolated_db):
    conn = sqlite3.connect(isolated_db)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO usage_sessions (kind, started_at, ended_at, duration_seconds) "
            "VALUES ('invalid', '2026-01-01', '2026-01-01', 1.0)"
        )
    conn.close()


def test_record_usage_session_inserts_row(isolated_db):
    db.record_usage_session(
        kind="stream",
        started_at="2026-09-01T10:00:00+00:00",
        ended_at="2026-09-01T10:05:00+00:00",
        duration_seconds=300.0,
        source="agent-123",
        detail={"correlation_id": "agent-123"},
    )
    conn = sqlite3.connect(isolated_db)
    row = conn.execute("SELECT kind, duration_seconds, source, detail_json FROM usage_sessions").fetchone()
    conn.close()
    assert row[0] == "stream"
    assert row[1] == 300.0
    assert row[2] == "agent-123"
    assert json.loads(row[3]) == {"correlation_id": "agent-123"}


def test_record_usage_session_swallows_errors(isolated_db):
    """A broken db path must not raise — playback/stream stop paths depend on this."""
    repo = UsageRepository("/nonexistent/dir/does-not-exist.db")
    repo.record_session(
        kind="music",
        started_at="2026-09-01T10:00:00+00:00",
        ended_at="2026-09-01T10:00:05+00:00",
        duration_seconds=5.0,
    )  # must not raise
