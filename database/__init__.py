"""
AnnounceFlow - Database Package
Modular repository pattern with backward compatible interface.

Old code: import database as db; db.get_all_media_files()
New code: from database import MediaRepository; MediaRepository().get_all_media_files()
"""
import sqlite3
import subprocess
import os
from datetime import datetime
from typing import Optional, List, Dict, Any

from .base_repository import BaseRepository
from .media_repository import MediaRepository
from .schedule_repository import ScheduleRepository
from .playback_repository import PlaybackRepository


# Database path
DATABASE_PATH = "announceflow.db"


# ============ SINGLETON REPOSITORY INSTANCES ============

_media_repo = MediaRepository(DATABASE_PATH)
_schedule_repo = ScheduleRepository(DATABASE_PATH)
_playback_repo = PlaybackRepository(DATABASE_PATH)


# ============ UTILITY FUNCTIONS ============


def _get_audio_duration(file_path: str) -> int:
    """Get audio duration in seconds using ffprobe."""
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "csv=p=0",
                file_path,
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode == 0 and result.stdout.strip():
            return int(float(result.stdout.strip()))
    except Exception:
        pass
    return 0


def _backfill_durations():
    """Backfill missing duration values for existing media files."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT id, filepath FROM media_files WHERE duration_seconds = 0 OR duration_seconds IS NULL"
    )
    rows = cursor.fetchall()

    for row in rows:
        mid, fpath = row["id"], row["filepath"]
        if os.path.exists(fpath):
            duration = _get_audio_duration(fpath)
            if duration > 0:
                cursor.execute(
                    "UPDATE media_files SET duration_seconds = ? WHERE id = ?",
                    (duration, mid),
                )

    conn.commit()
    conn.close()


def get_db_connection():
    """Get database connection with row factory."""
    conn = sqlite3.connect(DATABASE_PATH)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.row_factory = sqlite3.Row
    return conn


# ============ INITIALIZATION ============


def init_database():
    """Initialize database tables."""
    conn = get_db_connection()
    cursor = conn.cursor()

    # Media files table
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS media_files (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            filename TEXT NOT NULL,
            filepath TEXT NOT NULL,
            media_type TEXT NOT NULL CHECK(media_type IN ('music', 'announcement')),
            duration_seconds INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """
    )

    # One-time schedules table
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS one_time_schedules (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            media_id INTEGER NOT NULL,
            scheduled_datetime TIMESTAMP NOT NULL,
            reason TEXT,
            status TEXT DEFAULT 'pending' CHECK(status IN ('pending', 'played', 'cancelled')),
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (media_id) REFERENCES media_files (id) ON DELETE CASCADE
        )
    """
    )

    # Indexes for one_time_schedules (performance optimization)
    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_one_time_schedules_media_id
        ON one_time_schedules(media_id)
    """
    )
    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_one_time_schedules_status
        ON one_time_schedules(status)
    """
    )
    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_one_time_schedules_datetime
        ON one_time_schedules(scheduled_datetime)
    """
    )

    # Recurring schedules table
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS recurring_schedules (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            media_id INTEGER NOT NULL,
            days_of_week TEXT NOT NULL,
            start_time TEXT NOT NULL,
            end_time TEXT,
            interval_minutes INTEGER DEFAULT 0,
            specific_times TEXT,
            reason TEXT,
            is_active INTEGER DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (media_id) REFERENCES media_files (id) ON DELETE CASCADE
        )
    """
    )

    # Indexes for recurring_schedules (performance optimization)
    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_recurring_schedules_media_id
        ON recurring_schedules(media_id)
    """
    )
    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_recurring_schedules_active
        ON recurring_schedules(is_active)
    """
    )

    # Playback state table (single row for current state)
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS playback_state (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            current_media_id INTEGER,
            position_seconds REAL DEFAULT 0,
            is_playing INTEGER DEFAULT 0,
            volume INTEGER DEFAULT 80,
            last_nonzero_volume INTEGER DEFAULT 80,
            volume_revision INTEGER DEFAULT 0,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (current_media_id) REFERENCES media_files (id) ON DELETE SET NULL
        )
    """
    )

    # Initialize playback state if not exists
    cursor.execute(
        """
        INSERT OR IGNORE INTO playback_state (id, volume)
        VALUES (1, 80)
    """
    )

    conn.commit()
    conn.close()

    # Run migrations
    _run_migrations()

    # Backfill missing duration values
    _backfill_durations()


def _run_migrations():
    """Run database migrations for schema updates."""
    conn = get_db_connection()
    cursor = conn.cursor()

    # Migration: Add duration_seconds column to media_files if it doesn't exist
    try:
        cursor.execute("SELECT duration_seconds FROM media_files LIMIT 1")
    except sqlite3.OperationalError:
        cursor.execute(
            "ALTER TABLE media_files ADD COLUMN duration_seconds INTEGER DEFAULT 0"
        )
        conn.commit()

    # Migration: Add 'reason' column to one_time_schedules if it doesn't exist
    try:
        cursor.execute("SELECT reason FROM one_time_schedules LIMIT 1")
    except sqlite3.OperationalError:
        # Column doesn't exist, add it
        cursor.execute("ALTER TABLE one_time_schedules ADD COLUMN reason TEXT")
        conn.commit()

    # Migration: Add playlist state columns to playback_state
    try:
        cursor.execute("SELECT playlist_json FROM playback_state LIMIT 1")
    except sqlite3.OperationalError:
        cursor.execute("ALTER TABLE playback_state ADD COLUMN playlist_json TEXT")
        cursor.execute(
            "ALTER TABLE playback_state ADD COLUMN playlist_index INTEGER DEFAULT -1"
        )
        cursor.execute(
            "ALTER TABLE playback_state ADD COLUMN playlist_loop INTEGER DEFAULT 1"
        )
        cursor.execute(
            "ALTER TABLE playback_state ADD COLUMN playlist_active INTEGER DEFAULT 0"
        )
        conn.commit()

    # Migration: Add last_nonzero_volume column to playback_state
    try:
        cursor.execute("SELECT last_nonzero_volume FROM playback_state LIMIT 1")
    except sqlite3.OperationalError:
        cursor.execute(
            "ALTER TABLE playback_state ADD COLUMN last_nonzero_volume INTEGER DEFAULT 80"
        )
        conn.commit()

    # Migration: Add volume_revision column to playback_state
    try:
        cursor.execute("SELECT volume_revision FROM playback_state LIMIT 1")
    except sqlite3.OperationalError:
        cursor.execute(
            "ALTER TABLE playback_state ADD COLUMN volume_revision INTEGER DEFAULT 0"
        )
        conn.commit()

    # Backfill canonical volume columns for existing rows.
    cursor.execute(
        """
        UPDATE playback_state
        SET last_nonzero_volume = CASE
            WHEN COALESCE(last_nonzero_volume, 0) > 0 THEN last_nonzero_volume
            WHEN COALESCE(volume, 0) > 0 THEN volume
            ELSE 80
        END
        WHERE id = 1
    """
    )
    cursor.execute(
        """
        UPDATE playback_state
        SET volume_revision = COALESCE(volume_revision, 0)
        WHERE id = 1
    """
    )
    conn.commit()

    # Migration: Add 'reason' column to recurring_schedules if it doesn't exist
    try:
        cursor.execute("SELECT reason FROM recurring_schedules LIMIT 1")
    except sqlite3.OperationalError:
        cursor.execute("ALTER TABLE recurring_schedules ADD COLUMN reason TEXT")
        conn.commit()

    conn.close()


# ============ BACKWARD COMPATIBLE DELEGATE FUNCTIONS ============


# Media Files (5 functions)
def add_media_file(
    filename: str, filepath: str, media_type: str, duration_seconds: int = 0
) -> int:
    """Add a new media file to the database."""
    return _media_repo.add_media_file(filename, filepath, media_type, duration_seconds)


def get_all_media_files(media_type: Optional[str] = None) -> List[Dict[str, Any]]:
    """Get all media files, optionally filtered by type."""
    return _media_repo.get_all_media_files(media_type)


def get_media_file(media_id: int) -> Optional[Dict[str, Any]]:
    """Get a single media file by ID."""
    return _media_repo.get_media_file(media_id)


def get_media_by_filename(filename: str) -> Optional[Dict[str, Any]]:
    """Get a media file by filename."""
    return _media_repo.get_media_by_filename(filename)


def delete_media_file(media_id: int) -> bool:
    """Delete a media file by ID."""
    return _media_repo.delete_media_file(media_id)


# One-Time Schedules (5 functions)
def add_one_time_schedule(
    media_id: int, scheduled_datetime: datetime, reason: Optional[str] = None
) -> int:
    """Add a one-time schedule."""
    return _schedule_repo.add_one_time_schedule(media_id, scheduled_datetime, reason)


def get_pending_one_time_schedules() -> List[Dict[str, Any]]:
    """Get all pending one-time schedules."""
    return _schedule_repo.get_pending_one_time_schedules()


def get_all_one_time_schedules() -> List[Dict[str, Any]]:
    """Get all one-time schedules."""
    return _schedule_repo.get_all_one_time_schedules()


def update_one_time_schedule_status(schedule_id: int, status: str) -> bool:
    """Update status of a one-time schedule."""
    return _schedule_repo.update_one_time_schedule_status(schedule_id, status)


def delete_one_time_schedule(schedule_id: int) -> bool:
    """Delete a one-time schedule."""
    return _schedule_repo.delete_one_time_schedule(schedule_id)


def delete_one_time_schedules(schedule_ids: List[int]) -> int:
    """Delete multiple one-time schedules."""
    return _schedule_repo.delete_one_time_schedules(schedule_ids)


# Recurring Schedules (6 functions)
def add_recurring_schedule(
    media_id: int,
    days_of_week: List[int],
    start_time: str,
    end_time: Optional[str] = None,
    interval_minutes: int = 0,
    specific_times: Optional[List[str]] = None,
    reason: Optional[str] = None,
) -> int:
    """Add a recurring schedule."""
    return _schedule_repo.add_recurring_schedule(
        media_id,
        days_of_week,
        start_time,
        end_time,
        interval_minutes,
        specific_times,
        reason,
    )


def get_active_recurring_schedules() -> List[Dict[str, Any]]:
    """Get all active recurring schedules."""
    return _schedule_repo.get_active_recurring_schedules()


def get_all_recurring_schedules() -> List[Dict[str, Any]]:
    """Get all recurring schedules."""
    return _schedule_repo.get_all_recurring_schedules()


def toggle_recurring_schedule(schedule_id: int, is_active: bool) -> bool:
    """Enable or disable a recurring schedule."""
    return _schedule_repo.toggle_recurring_schedule(schedule_id, is_active)


def delete_recurring_schedule(schedule_id: int) -> bool:
    """Delete a recurring schedule."""
    return _schedule_repo.delete_recurring_schedule(schedule_id)


def delete_recurring_schedules(schedule_ids: List[int]) -> int:
    """Delete multiple recurring schedules."""
    return _schedule_repo.delete_recurring_schedules(schedule_ids)


def delete_all_recurring_announcements() -> int:
    """Delete all recurring announcement schedules (not music)."""
    return _schedule_repo.delete_all_recurring_announcements()


# Playback State (2 functions)
def get_playback_state() -> Dict[str, Any]:
    """Get current playback state."""
    return _playback_repo.get_playback_state()


def update_playback_state(
    current_media_id: Optional[int] = None,
    position_seconds: Optional[float] = None,
    is_playing: Optional[bool] = None,
    volume: Optional[int] = None,
) -> bool:
    """Update playback state."""
    return _playback_repo.update_playback_state(
        current_media_id, position_seconds, is_playing, volume
    )


def get_volume_state() -> Dict[str, Any]:
    """Get canonical volume state."""
    return _playback_repo.get_volume_state()


def set_volume_state(volume: int) -> Dict[str, Any]:
    """Set canonical volume state atomically."""
    return _playback_repo.set_volume_state(volume)


# Playlist State (2 functions)
def save_playlist_state(
    playlist: Optional[List[str]] = None,
    index: Optional[int] = None,
    loop: Optional[bool] = None,
    active: Optional[bool] = None,
) -> bool:
    """Save playlist state to database for persistence across restarts."""
    return _playback_repo.save_playlist_state(playlist, index, loop, active)


def get_playlist_state() -> Dict[str, Any]:
    """Get saved playlist state from database."""
    return _playback_repo.get_playlist_state()
