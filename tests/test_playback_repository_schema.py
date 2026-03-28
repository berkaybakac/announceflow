"""Tests for PlaybackRepository schema detection via PRAGMA.

Regression: get_volume_state() and set_volume_state() previously detected
the new schema (volume_revision column) by catching OperationalError.
Under DB load, a genuine "database is locked" OperationalError would
incorrectly trigger the old-schema fallback, resetting volume_revision to 0
and causing unexpected volume changes for the customer.

Fix: _has_volume_revision_column() uses PRAGMA table_info() — a read-only
metadata query that never fails due to write contention.
"""
import sqlite3
from unittest.mock import patch

import pytest

from database.playback_repository import PlaybackRepository


# --------------- helpers ---------------


def _make_repo(db_path: str) -> PlaybackRepository:
    return PlaybackRepository(db_path=db_path)


def _create_new_schema(db_path: str, initial_volume: int = 80, initial_revision: int = 5):
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        CREATE TABLE playback_state (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            volume INTEGER DEFAULT 80,
            last_nonzero_volume INTEGER DEFAULT 80,
            volume_revision INTEGER DEFAULT 0,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """
    )
    conn.execute(
        "INSERT INTO playback_state (id, volume, last_nonzero_volume, volume_revision) VALUES (1, ?, ?, ?)",
        (initial_volume, initial_volume, initial_revision),
    )
    conn.commit()
    conn.close()


def _create_old_schema(db_path: str, initial_volume: int = 80):
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
    conn.execute(
        "INSERT INTO playback_state (id, volume) VALUES (1, ?)",
        (initial_volume,),
    )
    conn.commit()
    conn.close()


# --------------- _has_volume_revision_column ---------------


class TestHasVolumeRevisionColumn:
    def test_returns_true_for_new_schema(self, tmp_path):
        db_path = str(tmp_path / "new.db")
        _create_new_schema(db_path)
        repo = _make_repo(db_path)
        conn = repo.get_connection()
        cursor = conn.cursor()
        result = repo._has_volume_revision_column(cursor)
        conn.close()
        assert result is True

    def test_returns_false_for_old_schema(self, tmp_path):
        db_path = str(tmp_path / "old.db")
        _create_old_schema(db_path)
        repo = _make_repo(db_path)
        conn = repo.get_connection()
        cursor = conn.cursor()
        result = repo._has_volume_revision_column(cursor)
        conn.close()
        assert result is False


# --------------- get_volume_state ---------------


class TestGetVolumeStateNewSchema:
    def test_returns_correct_volume(self, tmp_path):
        db_path = str(tmp_path / "test.db")
        _create_new_schema(db_path, initial_volume=75, initial_revision=3)
        repo = _make_repo(db_path)
        state = repo.get_volume_state()
        assert state["volume"] == 75

    def test_returns_revision_from_db(self, tmp_path):
        db_path = str(tmp_path / "test.db")
        _create_new_schema(db_path, initial_volume=75, initial_revision=3)
        repo = _make_repo(db_path)
        state = repo.get_volume_state()
        assert state["volume_revision"] == 3

    def test_returns_last_nonzero(self, tmp_path):
        db_path = str(tmp_path / "test.db")
        _create_new_schema(db_path, initial_volume=60)
        repo = _make_repo(db_path)
        state = repo.get_volume_state()
        assert state["last_nonzero_volume"] == 60

    def test_muted_flag_when_volume_zero(self, tmp_path):
        db_path = str(tmp_path / "test.db")
        _create_new_schema(db_path, initial_volume=0)
        repo = _make_repo(db_path)
        state = repo.get_volume_state()
        assert state["muted"] is True


class TestGetVolumeStateOldSchema:
    def test_returns_correct_volume(self, tmp_path):
        db_path = str(tmp_path / "old.db")
        _create_old_schema(db_path, initial_volume=55)
        repo = _make_repo(db_path)
        state = repo.get_volume_state()
        assert state["volume"] == 55

    def test_revision_is_zero_on_old_schema(self, tmp_path):
        """Old schema has no revision column — must default to 0, not crash."""
        db_path = str(tmp_path / "old.db")
        _create_old_schema(db_path)
        repo = _make_repo(db_path)
        state = repo.get_volume_state()
        assert state["volume_revision"] == 0


# --------------- set_volume_state ---------------


class TestSetVolumeStateNewSchema:
    def test_increments_revision(self, tmp_path):
        db_path = str(tmp_path / "test.db")
        _create_new_schema(db_path, initial_volume=80, initial_revision=5)
        repo = _make_repo(db_path)
        result = repo.set_volume_state(60)
        assert result["volume"] == 60
        assert result["volume_revision"] == 6

    def test_preserves_last_nonzero_when_muting(self, tmp_path):
        db_path = str(tmp_path / "test.db")
        _create_new_schema(db_path, initial_volume=70)
        repo = _make_repo(db_path)
        result = repo.set_volume_state(0)
        assert result["volume"] == 0
        assert result["muted"] is True
        assert result["last_nonzero_volume"] == 70

    def test_persists_to_db(self, tmp_path):
        db_path = str(tmp_path / "test.db")
        _create_new_schema(db_path, initial_volume=80)
        repo = _make_repo(db_path)
        repo.set_volume_state(45)
        conn = sqlite3.connect(db_path)
        row = conn.execute("SELECT volume FROM playback_state WHERE id = 1").fetchone()
        conn.close()
        assert row[0] == 45


class TestSetVolumeStateOldSchema:
    def test_sets_volume(self, tmp_path):
        db_path = str(tmp_path / "old.db")
        _create_old_schema(db_path, initial_volume=80)
        repo = _make_repo(db_path)
        result = repo.set_volume_state(50)
        assert result["volume"] == 50

    def test_revision_is_zero_on_old_schema(self, tmp_path):
        db_path = str(tmp_path / "old.db")
        _create_old_schema(db_path, initial_volume=80)
        repo = _make_repo(db_path)
        result = repo.set_volume_state(50)
        assert result["volume_revision"] == 0

    def test_persists_to_db(self, tmp_path):
        db_path = str(tmp_path / "old.db")
        _create_old_schema(db_path, initial_volume=80)
        repo = _make_repo(db_path)
        repo.set_volume_state(45)
        conn = sqlite3.connect(db_path)
        row = conn.execute("SELECT volume FROM playback_state WHERE id = 1").fetchone()
        conn.close()
        assert row[0] == 45


# --------------- Regression: DB error no longer misidentified as old schema ---------------


class TestSchemaDetectionRegression:
    def test_db_error_during_update_propagates_not_silently_swallowed(self, monkeypatch):
        """A genuine OperationalError (e.g. write lock) must NOT trigger old-schema fallback.

        Old code: any OperationalError → silently use old schema path → revision reset to 0.
        New code: OperationalError during UPDATE propagates so the caller knows it failed.

        We mock the connection entirely so sqlite3.Connection C-level limits don't apply.
        """
        from unittest.mock import MagicMock

        # Build a mock cursor that simulates new schema via PRAGMA
        # but raises "database is locked" on UPDATE.
        pragma_row = MagicMock()
        pragma_row.__getitem__ = MagicMock(
            side_effect=lambda i: "volume_revision" if i == 1 else "id"
        )

        data_row = MagicMock()
        data_row.__getitem__ = MagicMock(
            side_effect=lambda key: {
                "volume": 65,
                "last_nonzero_volume": 65,
                "volume_revision": 7,
            }.get(key, 0)
        )

        cursor_mock = MagicMock()
        cursor_mock.fetchall.return_value = [pragma_row]
        cursor_mock.fetchone.return_value = data_row

        def execute_side(sql, *args):
            if "UPDATE" in sql.upper():
                raise sqlite3.OperationalError("database is locked")

        cursor_mock.execute.side_effect = execute_side

        conn_mock = MagicMock()
        conn_mock.cursor.return_value = cursor_mock

        repo = PlaybackRepository(db_path=":memory:")
        monkeypatch.setattr(repo, "get_connection", lambda: conn_mock)

        with pytest.raises(sqlite3.OperationalError, match="database is locked"):
            repo.set_volume_state(50)

    def test_pragma_called_once_per_operation(self, tmp_path, monkeypatch):
        """_has_volume_revision_column (PRAGMA) is called exactly once per read/write op."""
        db_path = str(tmp_path / "test.db")
        _create_new_schema(db_path, initial_volume=65)
        repo = _make_repo(db_path)

        call_count = [0]
        original = PlaybackRepository._has_volume_revision_column

        def counting_check(_, cursor):
            call_count[0] += 1
            return original(cursor)

        monkeypatch.setattr(PlaybackRepository, "_has_volume_revision_column", counting_check)

        repo.get_volume_state()
        repo.set_volume_state(70)

        assert call_count[0] == 2, "PRAGMA must be called once per get and once per set"
