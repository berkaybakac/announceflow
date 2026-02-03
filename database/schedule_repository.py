"""
Schedule repository for one-time and recurring schedule operations.
"""
import json
from datetime import datetime
from typing import Optional, List, Dict, Any
from .base_repository import BaseRepository


class ScheduleRepository(BaseRepository):
    """Repository for schedule operations (one-time and recurring)."""

    # ============ ONE-TIME SCHEDULES ============

    def add_one_time_schedule(self, media_id: int, scheduled_datetime: datetime, reason: Optional[str] = None) -> int:
        """Add a one-time schedule."""
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO one_time_schedules (media_id, scheduled_datetime, reason)
            VALUES (?, ?, ?)
        ''', (media_id, scheduled_datetime.isoformat(), reason))
        schedule_id = cursor.lastrowid or 0
        conn.commit()
        conn.close()
        return schedule_id

    def get_pending_one_time_schedules(self) -> List[Dict[str, Any]]:
        """Get all pending one-time schedules with media info."""
        conn = self.get_connection()
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

    def get_all_one_time_schedules(self) -> List[Dict[str, Any]]:
        """Get all one-time schedules with media info."""
        conn = self.get_connection()
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

    def update_one_time_schedule_status(self, schedule_id: int, status: str) -> bool:
        """Update status of a one-time schedule."""
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('''
            UPDATE one_time_schedules SET status = ? WHERE id = ?
        ''', (status, schedule_id))
        updated = cursor.rowcount > 0
        conn.commit()
        conn.close()
        return updated

    def delete_one_time_schedule(self, schedule_id: int) -> bool:
        """Delete a one-time schedule."""
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('DELETE FROM one_time_schedules WHERE id = ?', (schedule_id,))
        deleted = cursor.rowcount > 0
        conn.commit()
        conn.close()
        return deleted

    # ============ RECURRING SCHEDULES ============

    def add_recurring_schedule(
        self,
        media_id: int,
        days_of_week: List[int],
        start_time: str,
        end_time: Optional[str] = None,
        interval_minutes: int = 0,
        specific_times: Optional[List[str]] = None
    ) -> int:
        """Add a recurring schedule."""
        conn = self.get_connection()
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

    def get_active_recurring_schedules(self) -> List[Dict[str, Any]]:
        """Get all active recurring schedules with media info."""
        conn = self.get_connection()
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

    def get_all_recurring_schedules(self) -> List[Dict[str, Any]]:
        """Get all recurring schedules with media info."""
        conn = self.get_connection()
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

    def toggle_recurring_schedule(self, schedule_id: int, is_active: bool) -> bool:
        """Enable or disable a recurring schedule."""
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('''
            UPDATE recurring_schedules SET is_active = ? WHERE id = ?
        ''', (1 if is_active else 0, schedule_id))
        updated = cursor.rowcount > 0
        conn.commit()
        conn.close()
        return updated

    def delete_recurring_schedule(self, schedule_id: int) -> bool:
        """Delete a recurring schedule."""
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('DELETE FROM recurring_schedules WHERE id = ?', (schedule_id,))
        deleted = cursor.rowcount > 0
        conn.commit()
        conn.close()
        return deleted

    def delete_all_recurring_announcements(self) -> int:
        """Delete all recurring announcement schedules (not music)."""
        conn = self.get_connection()
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
