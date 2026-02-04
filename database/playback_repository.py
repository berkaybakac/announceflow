"""
Playback and playlist state repository.
"""
import json
from typing import Optional, List, Dict, Any
from .base_repository import BaseRepository


class PlaybackRepository(BaseRepository):
    """Repository for playback and playlist state operations."""

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
            updates.append("volume = ?")
            values.append(volume)

        if updates:
            updates.append("updated_at = CURRENT_TIMESTAMP")
            query = f"UPDATE playback_state SET {', '.join(updates)} WHERE id = 1"
            cursor.execute(query, values)
            conn.commit()

        conn.close()
        return True

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
