"""
Media files repository for CRUD operations.
"""
from typing import Optional, List, Dict, Any
from .base_repository import BaseRepository


class MediaRepository(BaseRepository):
    """Repository for media file operations."""

    def add_media_file(self, filename: str, filepath: str, media_type: str, duration_seconds: int = 0) -> int:
        """Add a new media file to the database.

        Args:
            filename: Name of the media file
            filepath: Full path to the media file
            media_type: Type of media ('music' or 'announcement')
            duration_seconds: Duration in seconds (default: 0)

        Returns:
            ID of the newly created media file
        """
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO media_files (filename, filepath, media_type, duration_seconds)
            VALUES (?, ?, ?, ?)
        ''', (filename, filepath, media_type, duration_seconds))
        media_id = cursor.lastrowid or 0
        conn.commit()
        conn.close()
        return media_id

    def get_all_media_files(self, media_type: Optional[str] = None) -> List[Dict[str, Any]]:
        """Get all media files, optionally filtered by type.

        Args:
            media_type: Optional filter by type ('music' or 'announcement')

        Returns:
            List of media file dictionaries
        """
        conn = self.get_connection()
        cursor = conn.cursor()

        if media_type:
            cursor.execute('SELECT * FROM media_files WHERE media_type = ? ORDER BY created_at DESC', (media_type,))
        else:
            cursor.execute('SELECT * FROM media_files ORDER BY created_at DESC')

        rows = cursor.fetchall()
        conn.close()
        return [dict(row) for row in rows]

    def get_media_file(self, media_id: int) -> Optional[Dict[str, Any]]:
        """Get a single media file by ID.

        Args:
            media_id: Media file ID

        Returns:
            Media file dictionary or None if not found
        """
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM media_files WHERE id = ?', (media_id,))
        row = cursor.fetchone()
        conn.close()
        return dict(row) if row else None

    def get_media_by_filename(self, filename: str) -> Optional[Dict[str, Any]]:
        """Get a media file by filename.

        Args:
            filename: Name of the media file

        Returns:
            Media file dictionary or None if not found
        """
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM media_files WHERE filename = ?', (filename,))
        row = cursor.fetchone()
        conn.close()
        return dict(row) if row else None

    def delete_media_file(self, media_id: int) -> bool:
        """Delete a media file by ID.

        Args:
            media_id: Media file ID to delete

        Returns:
            True if deleted, False otherwise
        """
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('DELETE FROM media_files WHERE id = ?', (media_id,))
        deleted = cursor.rowcount > 0
        conn.commit()
        conn.close()
        return deleted
