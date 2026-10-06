"""Upgrading a real old-version DB with today's init_database().

Fixtures in tests/fixtures/announceflow_db_<tag>.sql are `.dump`s of DBs
created and seeded by the *actual* code at that tag (git archive <tag>, then
init_database + add_media_file/add_*_schedule/save_playlist_state/volume).
v2.2.0 is the oldest version known to have run in the field; v1.0.0 is the
first release.
"""
import sqlite3
from pathlib import Path

import pytest

import database as db

FIXTURES = Path(__file__).parent / "fixtures"


def _table_columns(path: str) -> dict:
    conn = sqlite3.connect(path)
    try:
        tables = [
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
        ]
        return {t: {r[1] for r in conn.execute(f"PRAGMA table_info({t})")} for t in tables}
    finally:
        conn.close()


@pytest.fixture(params=["v1.0.0", "v2.2.0"])
def upgraded_db(request, temp_db, repoint_db, tmp_path):
    """Load an old-version dump, then run today's init_database() twice."""
    path = str(tmp_path / f"old_{request.param}.db")
    conn = sqlite3.connect(path)
    conn.executescript((FIXTURES / f"announceflow_db_{request.param}.sql").read_text())
    conn.close()

    repoint_db(path)  # temp_db fixture restores the session DB afterwards
    db.init_database()
    db.init_database()  # must be idempotent on an already-upgraded DB
    return request.param, path


def test_schema_matches_fresh_install(upgraded_db, temp_db):
    _, old_path = upgraded_db
    assert _table_columns(old_path) == _table_columns(temp_db)


def test_media_and_schedules_survive_upgrade(upgraded_db):
    media = {m["filename"]: m for m in db.get_all_media_files()}
    assert set(media) == {"song.mp3", "anons.mp3"}
    assert media["song.mp3"]["media_type"] == "music"
    assert media["song.mp3"]["duration_seconds"] == 180

    (one_time,) = db.get_all_one_time_schedules()
    # Stored as naive Istanbul local time by old versions → UTC Z today.
    assert one_time["scheduled_datetime"] == "2026-03-01T07:30:00Z"
    assert one_time["status"] == "pending"
    assert one_time["reason"] == "kampanya"

    recurring = sorted(db.get_all_recurring_schedules(), key=lambda s: s["id"])
    assert [(s["days_of_week"], s["specific_times"], s["interval_minutes"], s["is_active"])
            for s in recurring] == [
        ([0, 1, 2, 3, 4], ["09:00", "13:00"], 0, 1),
        ([5, 6], None, 30, 1),
    ]


def test_playlist_and_volume_survive_upgrade(upgraded_db):
    tag, _ = upgraded_db

    playlist = db.get_playlist_state()
    assert playlist["active"] is True
    assert playlist["loop"] is True
    assert playlist["index"] == 0
    assert playlist["playlist"] == ["/home/admin/announceflow/media/music/song.mp3"]

    volume = db.get_volume_state()
    assert volume["volume"] == 37
    assert volume["muted"] is False
    if tag == "v1.0.0":
        # Current behaviour, not necessarily desired: the volume_state
        # migration seeds last_nonzero_volume with the default 80 instead of
        # the stored volume, so mute → unmute on such a DB jumps to 80.
        assert volume["last_nonzero_volume"] == 80
    else:
        assert volume["last_nonzero_volume"] == 37
