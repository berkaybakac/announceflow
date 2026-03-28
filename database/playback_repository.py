"""
Playback and playlist state repository.
"""
import sqlite3
import json
from typing import Optional, List, Dict, Any
from .base_repository import BaseRepository


class PlaybackRepository(BaseRepository):
    """Repository for playback and playlist state operations."""

    _DEFAULT_VOLUME = 80

    @staticmethod
    def _to_int(value: Any, default: int) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return int(default)

    @classmethod
    def _normalize_volume(cls, value: Any) -> int:
        parsed = cls._to_int(value, cls._DEFAULT_VOLUME)
        return max(0, min(100, parsed))

    @classmethod
    def _normalize_last_nonzero(cls, value: Any, fallback: int) -> int:
        parsed = cls._normalize_volume(value if value is not None else fallback)
        if parsed <= 0:
            parsed = max(1, cls._normalize_volume(fallback))
        return parsed

    @classmethod
    def _normalize_revision(cls, value: Any) -> int:
        return max(0, cls._to_int(value, 0))

    @staticmethod
    def _row_value(row: Any, key: str, default: Any = None) -> Any:
        if row is None:
            return default
        try:
            return row[key]
        except (KeyError, TypeError, IndexError):
            return default

    @staticmethod
    def _has_volume_revision_column(cursor) -> bool:
        """Check schema explicitly via PRAGMA — never infer from exceptions."""
        cursor.execute("PRAGMA table_info(playback_state)")
        return any(row[1] == "volume_revision" for row in cursor.fetchall())

    # ============ PLAYBACK STATE ============

    def get_playback_state(self) -> Dict[str, Any]:
        """Get current playback state with media info."""
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT ps.*, m.filename, m.filepath
            FROM playback_state ps
            LEFT JOIN media_files m ON ps.current_media_id = m.id
            WHERE ps.id = 1
        """
        )
        row = cursor.fetchone()
        conn.close()
        return dict(row) if row else {}

    def update_playback_state(
        self,
        current_media_id: Optional[int] = None,
        position_seconds: Optional[float] = None,
        is_playing: Optional[bool] = None,
        volume: Optional[int] = None,
    ) -> bool:
        """Update playback state."""
        conn = self.get_connection()
        cursor = conn.cursor()

        updates = []
        values = []

        if current_media_id is not None:
            updates.append("current_media_id = ?")
            try:
                mid = int(current_media_id)
                values.append(mid if mid > 0 else None)
            except (ValueError, TypeError):
                values.append(None)
        if position_seconds is not None:
            updates.append("position_seconds = ?")
            values.append(position_seconds)
        if is_playing is not None:
            updates.append("is_playing = ?")
            values.append(1 if is_playing else 0)
        if volume is not None:
            normalized_volume = self._normalize_volume(volume)
            updates.append("volume = ?")
            values.append(normalized_volume)
            if normalized_volume > 0:
                updates.append("last_nonzero_volume = ?")
                values.append(normalized_volume)
            updates.append("volume_revision = COALESCE(volume_revision, 0) + 1")

        if updates:
            updates.append("updated_at = CURRENT_TIMESTAMP")
            query = f"UPDATE playback_state SET {', '.join(updates)} WHERE id = 1"
            cursor.execute(query, values)
            conn.commit()

        conn.close()
        return True

    def get_volume_state(self) -> Dict[str, Any]:
        """Get canonical volume state used by all clients."""
        conn = self.get_connection()
        cursor = conn.cursor()
        row = None
        try:
            if self._has_volume_revision_column(cursor):
                cursor.execute(
                    """
                    INSERT OR IGNORE INTO playback_state (id, volume, last_nonzero_volume, volume_revision)
                    VALUES (1, 80, 80, 0)
                """
                )
                cursor.execute(
                    """
                    SELECT volume, last_nonzero_volume, volume_revision
                    FROM playback_state
                    WHERE id = 1
                """
                )
            else:
                # Pre-migration schema: no revision columns.
                cursor.execute(
                    """
                    INSERT OR IGNORE INTO playback_state (id, volume)
                    VALUES (1, 80)
                """
                )
                cursor.execute(
                    """
                    SELECT volume
                    FROM playback_state
                    WHERE id = 1
                """
                )
            row = cursor.fetchone()
            conn.commit()
        finally:
            conn.close()

        volume = self._normalize_volume(self._row_value(row, "volume"))
        last_nonzero = self._normalize_last_nonzero(
            self._row_value(row, "last_nonzero_volume"),
            fallback=(volume if volume > 0 else self._DEFAULT_VOLUME),
        )
        revision = self._normalize_revision(self._row_value(row, "volume_revision"))
        return {
            "volume": volume,
            "muted": volume <= 0,
            "last_nonzero_volume": last_nonzero,
            "volume_revision": revision,
        }

    def set_volume_state(self, volume: int) -> Dict[str, Any]:
        """Persist canonical volume state atomically."""
        conn = self.get_connection()
        cursor = conn.cursor()
        try:
            if self._has_volume_revision_column(cursor):
                cursor.execute(
                    """
                    INSERT OR IGNORE INTO playback_state (id, volume, last_nonzero_volume, volume_revision)
                    VALUES (1, 80, 80, 0)
                """
                )
                cursor.execute(
                    """
                    SELECT volume, last_nonzero_volume, volume_revision
                    FROM playback_state
                    WHERE id = 1
                """
                )
                row = cursor.fetchone()

                current_volume = self._normalize_volume(self._row_value(row, "volume"))
                current_last_nonzero = self._normalize_last_nonzero(
                    self._row_value(row, "last_nonzero_volume"),
                    fallback=(current_volume if current_volume > 0 else self._DEFAULT_VOLUME),
                )
                current_revision = self._normalize_revision(
                    self._row_value(row, "volume_revision")
                )

                next_volume = self._normalize_volume(volume)
                next_last_nonzero = (
                    next_volume if next_volume > 0 else current_last_nonzero
                )
                next_revision = current_revision + 1

                cursor.execute(
                    """
                    UPDATE playback_state
                    SET volume = ?,
                        last_nonzero_volume = ?,
                        volume_revision = ?,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = 1
                """,
                    (next_volume, next_last_nonzero, next_revision),
                )
            else:
                # Pre-migration schema: no revision columns.
                cursor.execute(
                    """
                    INSERT OR IGNORE INTO playback_state (id, volume)
                    VALUES (1, 80)
                """
                )
                cursor.execute("SELECT volume FROM playback_state WHERE id = 1")
                row = cursor.fetchone()

                current_volume = self._normalize_volume(self._row_value(row, "volume"))
                next_volume = self._normalize_volume(volume)
                next_last_nonzero = (
                    next_volume
                    if next_volume > 0
                    else (current_volume if current_volume > 0 else self._DEFAULT_VOLUME)
                )
                next_revision = 0

                cursor.execute(
                    """
                    UPDATE playback_state
                    SET volume = ?,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = 1
                """,
                    (next_volume,),
                )
            conn.commit()
        finally:
            conn.close()

        return {
            "volume": next_volume,
            "muted": next_volume <= 0,
            "last_nonzero_volume": next_last_nonzero,
            "volume_revision": next_revision,
        }

    # ============ PLAYLIST STATE ============

    def save_playlist_state(
        self,
        playlist: Optional[List[str]] = None,
        index: Optional[int] = None,
        loop: Optional[bool] = None,
        active: Optional[bool] = None,
    ) -> bool:
        """Save playlist state to database for persistence across restarts."""
        conn = self.get_connection()
        cursor = conn.cursor()

        updates = []
        values = []

        if playlist is not None:
            updates.append("playlist_json = ?")
            values.append(json.dumps(playlist) if playlist else None)
        if index is not None:
            updates.append("playlist_index = ?")
            values.append(index)
        if loop is not None:
            updates.append("playlist_loop = ?")
            values.append(1 if loop else 0)
        if active is not None:
            updates.append("playlist_active = ?")
            values.append(1 if active else 0)

        if updates:
            updates.append("updated_at = CURRENT_TIMESTAMP")
            query = f"UPDATE playback_state SET {', '.join(updates)} WHERE id = 1"
            cursor.execute(query, values)
            conn.commit()

        conn.close()
        return True

    def get_playlist_state(self) -> Dict[str, Any]:
        """Get saved playlist state from database."""
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT playlist_json, playlist_index, playlist_loop, playlist_active
            FROM playback_state WHERE id = 1
        """
        )
        row = cursor.fetchone()
        conn.close()

        if row:
            playlist_json = row["playlist_json"]
            return {
                "playlist": json.loads(playlist_json) if playlist_json else [],
                "index": row["playlist_index"]
                if row["playlist_index"] is not None
                else -1,
                "loop": bool(row["playlist_loop"])
                if row["playlist_loop"] is not None
                else True,
                "active": bool(row["playlist_active"])
                if row["playlist_active"] is not None
                else False,
            }
        return {"playlist": [], "index": -1, "loop": True, "active": False}
