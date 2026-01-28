"""
AnnounceFlow - Scheduler Service
Handles one-time and recurring schedule checking and triggering.
"""
import logging
import threading
import time
import json
import os
from datetime import datetime, timedelta
from typing import Optional, Callable

import database as db
from player import get_player

logger = logging.getLogger(__name__)

CONFIG_FILE = 'config.json'


def load_config():
    """Load configuration from file."""
    if not os.path.exists(CONFIG_FILE):
        return {}
    with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
        return json.load(f)


def is_within_working_hours(config: dict) -> bool:
    """Check if current time is within working hours."""
    if not config.get('working_hours_enabled', False):
        return True  # Not enabled, always allow
    
    start_str = config.get('working_hours_start', '09:00')
    end_str = config.get('working_hours_end', '22:00')
    
    try:
        now = datetime.now()
        current_time = now.strftime('%H:%M')
        
        # Parse times
        curr = datetime.strptime(current_time, '%H:%M').time()
        start = datetime.strptime(start_str, '%H:%M').time()
        end = datetime.strptime(end_str, '%H:%M').time()
        
        return start <= curr <= end
    except Exception as e:
        logger.error(f"Working hours check error: {e}")
        return True  # On error, allow playback


def is_prayer_time_active(config: dict) -> bool:
    """Check if current time is within a prayer time window."""
    if not config.get('prayer_times_enabled', False):
        return False  # Not enabled, no silence
    
    city = config.get('prayer_times_city', '')
    district = config.get('prayer_times_district', '')
    
    if not city:
        return False
    
    try:
        import prayer_times as pt
        return pt.is_prayer_time(city, district, buffer_minutes=1)
    except Exception as e:
        logger.error(f"Prayer times check error: {e}")
        return False  # On error, don't silence


class Scheduler:
    """Schedule manager for one-time and recurring playback."""
    
    def __init__(self, check_interval_seconds: int = 10):
        self.check_interval = check_interval_seconds
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._last_recurring_triggers: dict = {}  # {schedule_id: last_trigger_time}
        
    def start(self):
        """Start the scheduler background thread."""
        if self._running:
            logger.warning("Scheduler already running")
            return
        
        self._running = True
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()
        logger.info("Scheduler started")
    
    def stop(self):
        """Stop the scheduler."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=5)
        logger.info("Scheduler stopped")
    
    def _run_loop(self):
        """Main scheduler loop."""
        while self._running:
            try:
                config = load_config()
                
                # Check if we should be silent
                if not is_within_working_hours(config):
                    logger.debug("Outside working hours - skipping schedule check")
                    time.sleep(self.check_interval)
                    continue
                
                if is_prayer_time_active(config):
                    logger.info("Prayer time active - pausing playback")
                    # Stop any current playback during prayer time
                    player = get_player()
                    if player.is_playing:
                        player.stop()
                    time.sleep(self.check_interval)
                    continue
                
                self._check_one_time_schedules()
                self._check_recurring_schedules()
            except Exception as e:
                logger.error(f"Scheduler error: {e}")
            
            time.sleep(self.check_interval)
    
    def _check_one_time_schedules(self):
        """Check and trigger pending one-time schedules."""
        now = datetime.now()
        pending = db.get_pending_one_time_schedules()
        
        for schedule in pending:
            scheduled_dt = datetime.fromisoformat(schedule['scheduled_datetime'])
            
            # Check if it's time (within 1 minute tolerance)
            time_diff = (now - scheduled_dt).total_seconds()
            
            if 0 <= time_diff <= 60:
                # Time to play!
                logger.info(f"Triggering one-time schedule: {schedule['filename']}")
                self._play_media(schedule['filepath'], schedule['id'], is_one_time=True)
            elif time_diff > 300:
                # Missed by more than 5 minutes, mark as cancelled
                logger.warning(f"Schedule missed, cancelling: {schedule['filename']} (was scheduled for {scheduled_dt})")
                db.update_one_time_schedule_status(schedule['id'], 'cancelled')
    
    def _check_recurring_schedules(self):
        """Check and trigger active recurring schedules."""
        now = datetime.now()
        current_day = now.weekday()  # Monday=0, Sunday=6
        current_time = now.strftime('%H:%M')
        
        active_schedules = db.get_active_recurring_schedules()
        
        for schedule in active_schedules:
            schedule_id = schedule['id']
            days = schedule['days_of_week']
            
            # Check if today is in the scheduled days
            if current_day not in days:
                continue
            
            should_trigger = False
            
            # Check specific times
            if schedule.get('specific_times'):
                for scheduled_time in schedule['specific_times']:
                    if self._times_match(current_time, scheduled_time):
                        should_trigger = True
                        break
            
            # Check interval-based (between start and end time)
            elif schedule.get('interval_minutes') and schedule['interval_minutes'] > 0:
                start_time = schedule['start_time']
                end_time = schedule.get('end_time') or '23:59'
                interval = schedule['interval_minutes']
                
                if self._is_time_in_range(current_time, start_time, end_time):
                    # Check if enough time has passed since last trigger
                    last_trigger = self._last_recurring_triggers.get(schedule_id)
                    if last_trigger is None:
                        # First trigger - check if we're at a valid interval point
                        should_trigger = self._is_interval_point(current_time, start_time, interval)
                    else:
                        elapsed = (now - last_trigger).total_seconds() / 60
                        if elapsed >= interval - 1:  # 1 minute tolerance
                            should_trigger = True
            
            # Trigger if conditions met
            if should_trigger:
                # Avoid double-triggering within same minute
                last_trigger = self._last_recurring_triggers.get(schedule_id)
                if last_trigger and (now - last_trigger).total_seconds() < 55:
                    continue
                
                logger.info(f"Triggering recurring schedule: {schedule['filename']}")
                self._play_media(schedule['filepath'], schedule_id, is_one_time=False)
                self._last_recurring_triggers[schedule_id] = now
    
    def _play_media(self, filepath: str, schedule_id: int, is_one_time: bool):
        """Trigger media playback with announcement priority."""
        player = get_player()
        
        # Check if this is an announcement (based on file path or schedule type)
        is_announcement = '/announcements/' in filepath or '/announcement/' in filepath
        
        # If something is already playing
        if player.is_playing:
            if is_announcement:
                # For announcements: save current playlist state, stop current, play announcement
                playlist_was_active = player._playlist_active
                playlist_files = list(player._playlist) if player._playlist_active else []
                playlist_index = player._playlist_index
                playlist_loop = player._playlist_loop
                
                logger.info(f"Announcement interrupting - saving playlist state (active={playlist_was_active}, index={playlist_index})")
                
                # Stop but don't clear playlist
                player.stop()
                
                # Play announcement
                success = player.play(filepath)
                
                if success:
                    # Wait for announcement to finish, then restore playlist
                    def restore_playlist():
                        # Wait for current playback to finish
                        while player.is_playing:
                            time.sleep(0.5)
                        
                        # Restore playlist if it was active
                        if playlist_was_active and playlist_files:
                            logger.info(f"Announcement finished - resuming playlist from index {playlist_index + 1}")
                            player._playlist = playlist_files
                            player._playlist_loop = playlist_loop
                            # Resume from NEXT track (user requested this behavior)
                            next_idx = (playlist_index + 1) % len(playlist_files)
                            player._playlist_index = next_idx - 1  # Will be incremented by play_next
                            player._playlist_active = True
                            player.play_next()
                    
                    # Run restore in background thread
                    threading.Thread(target=restore_playlist, daemon=True).start()
            else:
                # For music: just interrupt
                logger.info("Interrupting current playback for scheduled music")
                player.stop()
                success = player.play(filepath)
        else:
            success = player.play(filepath)
        
        if success and is_one_time:
            db.update_one_time_schedule_status(schedule_id, 'played')
    
    def _times_match(self, current: str, scheduled: str) -> bool:
        """Check if two HH:MM times match."""
        return current == scheduled
    
    def _is_time_in_range(self, current: str, start: str, end: str) -> bool:
        """Check if current time is between start and end."""
        try:
            curr = datetime.strptime(current, '%H:%M').time()
            st = datetime.strptime(start, '%H:%M').time()
            en = datetime.strptime(end, '%H:%M').time()
            return st <= curr <= en
        except:
            return False
    
    def _is_interval_point(self, current: str, start: str, interval: int) -> bool:
        """Check if current time is at a valid interval point from start."""
        try:
            curr = datetime.strptime(current, '%H:%M')
            st = datetime.strptime(start, '%H:%M')
            diff_minutes = (curr - st).total_seconds() / 60
            return diff_minutes >= 0 and diff_minutes % interval < 1
        except:
            return False


# Singleton instance
_scheduler_instance: Optional[Scheduler] = None

def get_scheduler() -> Scheduler:
    """Get the singleton scheduler instance."""
    global _scheduler_instance
    if _scheduler_instance is None:
        _scheduler_instance = Scheduler()
    return _scheduler_instance


if __name__ == '__main__':
    # Test
    logging.basicConfig(level=logging.INFO)
    db.init_database()
    
    scheduler = get_scheduler()
    scheduler.start()
    
    print("Scheduler running. Press Ctrl+C to stop...")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        scheduler.stop()
        print("Stopped.")
