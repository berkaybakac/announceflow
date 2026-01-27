"""
AnnounceFlow - Scheduler Service
Handles one-time and recurring schedule checking and triggering.
"""
import logging
import threading
import time
import json
from datetime import datetime, timedelta
from typing import Optional, Callable

import database as db
from player import get_player

logger = logging.getLogger(__name__)


class Scheduler:
    """Schedule manager for one-time and recurring playback."""
    
    def __init__(self, check_interval_seconds: int = 30):
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
        """Trigger media playback."""
        player = get_player()
        
        # If something is already playing, we might want to queue or interrupt
        # For now, we'll interrupt
        if player.is_playing:
            logger.info("Interrupting current playback for scheduled item")
            player.stop()
        
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
