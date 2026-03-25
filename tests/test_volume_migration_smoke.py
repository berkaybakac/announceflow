"""Smoke test for playback volume column migrations."""

from __future__ import annotations

import sqlite3

import database as db


def test_init_database_migrates_playback_volume_columns(tmp_path, monkeypatch):
    db_path = tmp_path / "legacy_volume.db"

    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        CREATE TABLE playback_state (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            volume INTEGER DEFAULT 80,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """
    )
    conn.execute("INSERT INTO playback_state (id, volume) VALUES (1, 0)")
    conn.commit()
    conn.close()

    monkeypatch.setattr(db, "DATABASE_PATH", str(db_path))
    db.init_database()

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cols = {row["name"] for row in conn.execute("PRAGMA table_info(playback_state)")}
    row = conn.execute(
        """
        SELECT volume, last_nonzero_volume, volume_revision
        FROM playback_state
        WHERE id = 1
    """
    ).fetchone()
    conn.close()

    assert "last_nonzero_volume" in cols
    assert "volume_revision" in cols
    assert row["volume"] == 0
    assert row["last_nonzero_volume"] == 80
    assert row["volume_revision"] == 0

