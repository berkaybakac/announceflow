"""
AnnounceFlow - Database Models
SQLite database for persistent storage of media files and schedules.
"""
import sqlite3
import subprocess
import os
import json
from datetime import datetime
from typing import Optional, List, Dict, Any

DATABASE_PATH = 'announceflow.db'

def _get_audio_duration(file_path: str) -> int:
    """Get audio duration in seconds using ffprobe."""
    try:
        result = subprocess.run([
            'ffprobe', '-v', 'error', '-show_entries', 'format=duration',
            '-of', 'csv=p=0', file_path
        ], capture_output=True, text=True, timeout=30)
        if result.returncode == 0 and result.stdout.strip():
            return int(float(result.stdout.strip()))
    except Exception:
        pass
    return 0

def _backfill_durations():
    """Backfill missing duration values for existing media files."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT id, filepath FROM media_files WHERE duration_seconds = 0 OR duration_seconds IS NULL')
    rows = cursor.fetchall()
    
    for row in rows:
        mid, fpath = row['id'], row['filepath']
        if os.path.exists(fpath):
            duration = _get_audio_duration(fpath)
            if duration > 0:
                cursor.execute('UPDATE media_files SET duration_seconds = ? WHERE id = ?', (duration, mid))
    
    conn.commit()
    conn.close()

def get_db_connection():
    """Get database connection with row factory."""
    conn = sqlite3.connect(DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_database():
    """Initialize database tables."""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Media files table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS media_files (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            filename TEXT NOT NULL,
            filepath TEXT NOT NULL,
            media_type TEXT NOT NULL CHECK(media_type IN ('music', 'announcement')),
            duration_seconds INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # One-time schedules table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS one_time_schedules (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            media_id INTEGER NOT NULL,
            scheduled_datetime TIMESTAMP NOT NULL,
            reason TEXT,
            status TEXT DEFAULT 'pending' CHECK(status IN ('pending', 'played', 'cancelled')),
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (media_id) REFERENCES media_files (id) ON DELETE CASCADE
        )
    ''')
    
    # Recurring schedules table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS recurring_schedules (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            media_id INTEGER NOT NULL,
            days_of_week TEXT NOT NULL,
            start_time TEXT NOT NULL,
            end_time TEXT,
            interval_minutes INTEGER DEFAULT 0,
            specific_times TEXT,
            is_active INTEGER DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (media_id) REFERENCES media_files (id) ON DELETE CASCADE
        )
    ''')
    
    # Playback state table (single row for current state)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS playback_state (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            current_media_id INTEGER,
            position_seconds REAL DEFAULT 0,
            is_playing INTEGER DEFAULT 0,
            volume INTEGER DEFAULT 80,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (current_media_id) REFERENCES media_files (id) ON DELETE SET NULL
        )
    ''')
    
    # Initialize playback state if not exists
    cursor.execute('''
        INSERT OR IGNORE INTO playback_state (id, volume) VALUES (1, 80)
    ''')
    
    conn.commit()
    conn.close()
    
    # Backfill missing duration values
    _backfill_durations()
    
    # Run migrations
    _run_migrations()


def _run_migrations():
    """Run database migrations for schema updates."""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Migration: Add 'reason' column to one_time_schedules if it doesn't exist
    try:
        cursor.execute("SELECT reason FROM one_time_schedules LIMIT 1")
    except sqlite3.OperationalError:
        # Column doesn't exist, add it
        cursor.execute("ALTER TABLE one_time_schedules ADD COLUMN reason TEXT")
        conn.commit()
    
    conn.close()


# ============ MEDIA FILES ============

def add_media_file(filename: str, filepath: str, media_type: str, duration_seconds: int = 0) -> int:
    """Add a new media file to the database."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO media_files (filename, filepath, media_type, duration_seconds)
        VALUES (?, ?, ?, ?)
    ''', (filename, filepath, media_type, duration_seconds))
    media_id = cursor.lastrowid or 0
    conn.commit()
    conn.close()
    return media_id

def get_all_media_files(media_type: Optional[str] = None) -> List[Dict[str, Any]]:
    """Get all media files, optionally filtered by type."""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    if media_type:
        cursor.execute('SELECT * FROM media_files WHERE media_type = ? ORDER BY created_at DESC', (media_type,))
    else:
        cursor.execute('SELECT * FROM media_files ORDER BY created_at DESC')
    
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]

def get_media_file(media_id: int) -> Optional[Dict[str, Any]]:
    """Get a single media file by ID."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM media_files WHERE id = ?', (media_id,))
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None

def get_media_by_filename(filename: str) -> Optional[Dict[str, Any]]:
    """Get a media file by filename."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM media_files WHERE filename = ?', (filename,))
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None

def delete_media_file(media_id: int) -> bool:
    """Delete a media file by ID."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('DELETE FROM media_files WHERE id = ?', (media_id,))
    deleted = cursor.rowcount > 0
    conn.commit()
    conn.close()
    return deleted


# ============ ONE-TIME SCHEDULES ============

def add_one_time_schedule(media_id: int, scheduled_datetime: datetime, reason: Optional[str] = None) -> int:
    """Add a one-time schedule."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO one_time_schedules (media_id, scheduled_datetime, reason)
        VALUES (?, ?, ?)
    ''', (media_id, scheduled_datetime.isoformat(), reason))
    schedule_id = cursor.lastrowid or 0
    conn.commit()
    conn.close()
    return schedule_id

def get_pending_one_time_schedules() -> List[Dict[str, Any]]:
    """Get all pending one-time schedules."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT s.*, m.filename, m.filepath, m.media_type 
        FROM one_time_schedules s
        JOIN media_files m ON s.media_id = m.id
        WHERE s.status = 'pending'
        ORDER BY s.scheduled_datetime ASC
    ''')
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]

def get_all_one_time_schedules() -> List[Dict[str, Any]]:
    """Get all one-time schedules."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT s.*, m.filename, m.filepath, m.media_type 
        FROM one_time_schedules s
        JOIN media_files m ON s.media_id = m.id
        ORDER BY s.scheduled_datetime DESC
    ''')
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]

def update_one_time_schedule_status(schedule_id: int, status: str) -> bool:
    """Update status of a one-time schedule."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''
        UPDATE one_time_schedules SET status = ? WHERE id = ?
    ''', (status, schedule_id))
    updated = cursor.rowcount > 0
    conn.commit()
    conn.close()
    return updated

def delete_one_time_schedule(schedule_id: int) -> bool:
    """Delete a one-time schedule."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('DELETE FROM one_time_schedules WHERE id = ?', (schedule_id,))
    deleted = cursor.rowcount > 0
    conn.commit()
    conn.close()
    return deleted


# ============ RECURRING SCHEDULES ============

def add_recurring_schedule(
    media_id: int,
    days_of_week: List[int],
    start_time: str,
    end_time: Optional[str] = None,
    interval_minutes: int = 0,
    specific_times: Optional[List[str]] = None
) -> int:
    """
    Add a recurring schedule.
    days_of_week: List of integers 0-6 (Monday=0, Sunday=6)
    specific_times: List of times like ["10:00", "12:00", "15:00"]
    """
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO recurring_schedules (media_id, days_of_week, start_time, end_time, interval_minutes, specific_times)
        VALUES (?, ?, ?, ?, ?, ?)
    ''', (
        media_id,
        json.dumps(days_of_week),
        start_time,
        end_time,
        interval_minutes,
        json.dumps(specific_times) if specific_times else None
    ))
    schedule_id = cursor.lastrowid or 0
    conn.commit()
    conn.close()
    return schedule_id

def get_active_recurring_schedules() -> List[Dict[str, Any]]:
    """Get all active recurring schedules."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT s.*, m.filename, m.filepath, m.media_type 
        FROM recurring_schedules s
        JOIN media_files m ON s.media_id = m.id
        WHERE s.is_active = 1
        ORDER BY s.start_time ASC
    ''')
    rows = cursor.fetchall()
    conn.close()
    
    result = []
    for row in rows:
        item = dict(row)
        item['days_of_week'] = json.loads(item['days_of_week'])
        if item['specific_times']:
            item['specific_times'] = json.loads(item['specific_times'])
        result.append(item)
    return result

def get_all_recurring_schedules() -> List[Dict[str, Any]]:
    """Get all recurring schedules."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT s.*, m.filename, m.filepath, m.media_type 
        FROM recurring_schedules s
        JOIN media_files m ON s.media_id = m.id
        ORDER BY s.created_at DESC
    ''')
    rows = cursor.fetchall()
    conn.close()
    
    result = []
    for row in rows:
        item = dict(row)
        item['days_of_week'] = json.loads(item['days_of_week'])
        if item['specific_times']:
            item['specific_times'] = json.loads(item['specific_times'])
        result.append(item)
    return result

def toggle_recurring_schedule(schedule_id: int, is_active: bool) -> bool:
    """Enable or disable a recurring schedule."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''
        UPDATE recurring_schedules SET is_active = ? WHERE id = ?
    ''', (1 if is_active else 0, schedule_id))
    updated = cursor.rowcount > 0
    conn.commit()
    conn.close()
    return updated

def delete_recurring_schedule(schedule_id: int) -> bool:
    """Delete a recurring schedule."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('DELETE FROM recurring_schedules WHERE id = ?', (schedule_id,))
    deleted = cursor.rowcount > 0
    conn.commit()
    conn.close()
    return deleted

def delete_all_recurring_announcements() -> int:
    """Delete all recurring announcement schedules (not music)."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''
        DELETE FROM recurring_schedules
        WHERE media_id IN (
            SELECT id FROM media_files WHERE media_type = 'announcement'
        )
    ''')
    deleted_count = cursor.rowcount
    conn.commit()
    conn.close()
    return deleted_count


# ============ PLAYBACK STATE ============

def get_playback_state() -> Dict[str, Any]:
    """Get current playback state."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT ps.*, m.filename, m.filepath
        FROM playback_state ps
        LEFT JOIN media_files m ON ps.current_media_id = m.id
        WHERE ps.id = 1
    ''')
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else {}

def update_playback_state(
    current_media_id: Optional[int] = None,
    position_seconds: Optional[float] = None,
    is_playing: Optional[bool] = None,
    volume: Optional[int] = None
) -> bool:
    """Update playback state."""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    updates = []
    values = []
    
    if current_media_id is not None:
        updates.append('current_media_id = ?')
        values.append(current_media_id if current_media_id > 0 else None)
    if position_seconds is not None:
        updates.append('position_seconds = ?')
        values.append(position_seconds)
    if is_playing is not None:
        updates.append('is_playing = ?')
        values.append(1 if is_playing else 0)
    if volume is not None:
        updates.append('volume = ?')
        values.append(volume)
    
    if updates:
        updates.append('updated_at = CURRENT_TIMESTAMP')
        query = f"UPDATE playback_state SET {', '.join(updates)} WHERE id = 1"
        cursor.execute(query, values)
        conn.commit()
    
    conn.close()
    return True


# Initialize on import
if __name__ == '__main__':
    init_database()
    print("Database initialized successfully!")
