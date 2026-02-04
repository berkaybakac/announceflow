"""
AnnounceFlow - Scheduler Service
Handles one-time and recurring schedule checking and triggering.
"""
import logging
import threading
import time
from datetime import datetime
from typing import Optional

import database as db
from player import get_player
from logger import log_trigger, log_schedule, log_prayer, log_error
from services.config_service import load_config

logger = logging.getLogger(__name__)


def is_within_working_hours(config: dict) -> bool:
    """Check if current time is within working hours."""
    if not config.get("working_hours_enabled", False):
        return True  # Not enabled, always allow

    start_str = config.get("working_hours_start", "09:00")
    end_str = config.get("working_hours_end", "22:00")

    try:
        now = datetime.now()
        current_time = now.strftime("%H:%M")

        # Parse times
        curr = datetime.strptime(current_time, "%H:%M").time()
        start = datetime.strptime(start_str, "%H:%M").time()
        end = datetime.strptime(end_str, "%H:%M").time()

        return start <= curr <= end
    except Exception as e:
        logger.error(f"Working hours check error: {e}")
        return True  # On error, allow playback


def is_prayer_time_active(config: dict) -> bool:
    """Check if current time is within a prayer time window."""
    if not config.get("prayer_times_enabled", False):
        return False  # Not enabled, no silence

    city = config.get("prayer_times_city", "")
    district = config.get("prayer_times_district", "")

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
        self._prayer_pause_state: Optional[
            dict
        ] = None  # Playlist state saved during prayer time
        self._working_hours_pause_state: Optional[
            dict
        ] = None  # Playlist state saved outside working hours
        # Thread management for restore operations
        self._restore_threads: list = []
        self._restore_lock = threading.Lock()
        self._restore_in_progress = (
            False  # Idempotency: prevent multiple simultaneous restores
        )
        # Config caching to reduce disk I/O
        self._config_cache: Optional[dict] = None
        self._config_cache_time: float = 0
        self._config_cache_ttl: int = 30  # Reload config every 30 seconds max

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

    def _get_cached_config(self) -> dict:
        """Get config with TTL caching to reduce disk I/O."""
        now = time.time()
        if (
            self._config_cache is None
            or (now - self._config_cache_time) > self._config_cache_ttl
        ):
            self._config_cache = load_config()
            self._config_cache_time = now
        return self._config_cache

    def _handle_prayer_time(self, config: dict, player) -> bool:
        """Handle prayer time check and playlist pause/resume.

        Returns True if we should skip the rest of the loop (prayer time active).
        """
        if is_prayer_time_active(config):
            if player.is_playing or player._playlist_active:
                # Save playlist state BEFORE stopping (only once)
                if self._prayer_pause_state is None:
                    self._prayer_pause_state = {
                        "playlist": list(player._playlist),
                        "index": player._playlist_index,
                        "loop": player._playlist_loop,
                        "active": player._playlist_active,
                    }
                    logger.info(
                        f"Prayer time - saving playlist state (index={player._playlist_index}, tracks={len(player._playlist)})"
                    )
                    log_prayer(
                        "silence_start",
                        {
                            "index": player._playlist_index,
                            "tracks": len(player._playlist),
                        },
                    )
                # Use stop() instead of stop_playlist() to preserve DB state
                player.stop()
                player._playlist_active = False  # Temporarily disable without clearing
            return True  # Skip rest of loop

        # Prayer time ended - restore playlist if we saved state
        if self._prayer_pause_state is not None:
            state = self._prayer_pause_state
            self._prayer_pause_state = None

            if state["active"] and state["playlist"]:
                logger.info(
                    f"Prayer ended - restoring playlist (index={state['index']})"
                )
                log_prayer("silence_end", {"index": state["index"]})
                player._playlist = state["playlist"]
                player._playlist_loop = state["loop"]
                player._playlist_index = state["index"]
                player._playlist_active = True
                # Sync to DB and play next
                db.save_playlist_state(
                    playlist=state["playlist"],
                    index=state["index"],
                    loop=state["loop"],
                    active=True,
                )
                player.play_next()

        return False  # Continue with rest of loop

    def _handle_working_hours(self, config: dict, player) -> bool:
        """Handle working hours check and playlist pause/resume.

        Returns True if outside working hours (schedules should be limited).
        """
        outside_working_hours = not is_within_working_hours(config)

        if outside_working_hours:
            # Save playlist state BEFORE stopping (only once)
            if player._playlist_active or player.is_playing:
                if self._working_hours_pause_state is None:
                    self._working_hours_pause_state = {
                        "playlist": list(player._playlist),
                        "index": player._playlist_index,
                        "loop": player._playlist_loop,
                        "active": player._playlist_active,
                    }
                    logger.info(
                        f"Outside working hours - saving playlist state (index={player._playlist_index}, tracks={len(player._playlist)})"
                    )
                    log_schedule(
                        "working_hours_end",
                        {
                            "index": player._playlist_index,
                            "tracks": len(player._playlist),
                        },
                    )
                # Use stop() instead of stop_playlist() to preserve DB state
                player.stop()
                player._playlist_active = False  # Temporarily disable without clearing
        else:
            # Working hours started - restore playlist if we saved state
            if self._working_hours_pause_state is not None:
                state = self._working_hours_pause_state
                self._working_hours_pause_state = None

                if state["active"] and state["playlist"]:
                    logger.info(
                        f"Working hours started - restoring playlist (index={state['index']})"
                    )
                    log_schedule("working_hours_start", {"index": state["index"]})
                    player._playlist = state["playlist"]
                    player._playlist_loop = state["loop"]
                    player._playlist_index = state["index"]
                    player._playlist_active = True
                    # Sync to DB and play next
                    db.save_playlist_state(
                        playlist=state["playlist"],
                        index=state["index"],
                        loop=state["loop"],
                        active=True,
                    )
                    player.play_next()

        return outside_working_hours

    def _run_loop(self):
        """Main scheduler loop."""
        while self._running:
            try:
                config = self._get_cached_config()
                player = get_player()

                # 1. Prayer time check - highest priority, stops EVERYTHING
                if self._handle_prayer_time(config, player):
                    time.sleep(self.check_interval)
                    continue

                # 2. Working hours check
                outside_working_hours = self._handle_working_hours(config, player)

                # 3. Always check one-time schedules (announcements play even outside hours)
                self._check_one_time_schedules()

                # 4. Only check recurring schedules during working hours
                if not outside_working_hours:
                    self._check_recurring_schedules()

            except Exception as e:
                logger.error(f"Scheduler error: {e}")

            time.sleep(self.check_interval)

    def _check_one_time_schedules(self):
        """Check and trigger pending one-time schedules."""
        config = self._get_cached_config()
        outside_working_hours = not is_within_working_hours(config)

        now = datetime.now()
        pending = db.get_pending_one_time_schedules()

        for schedule in pending:
            # Outside working hours: only play announcements, not music
            if outside_working_hours and schedule.get("media_type") != "announcement":
                continue
            try:
                dt_str = schedule["scheduled_datetime"]
                # Handle multiple datetime formats
                dt_str = dt_str.replace("T", " ")
                try:
                    scheduled_dt = datetime.strptime(dt_str, "%Y-%m-%d %H:%M:%S")
                except ValueError:
                    scheduled_dt = datetime.strptime(dt_str, "%Y-%m-%d %H:%M")
            except Exception as e:
                # Invalid datetime format - mark as cancelled to prevent infinite retry
                logger.error(
                    f"Invalid datetime for schedule #{schedule['id']}: '{schedule['scheduled_datetime']}' - marking as cancelled"
                )
                db.update_one_time_schedule_status(schedule["id"], "cancelled")
                continue

            # Check if it's time (within 2 minute tolerance for safety)
            time_diff = (now - scheduled_dt).total_seconds()

            if 0 <= time_diff <= 120:
                # Time to play!
                logger.info(
                    f"Triggering one-time schedule: {schedule['filename']} (diff: {time_diff:.0f}s)"
                )
                log_trigger(
                    "one_time",
                    {
                        "filename": schedule["filename"],
                        "media_type": schedule.get("media_type", "music"),
                        "delay_seconds": int(time_diff),
                    },
                )
                self._play_media(
                    schedule["filepath"],
                    schedule["id"],
                    is_one_time=True,
                    is_announcement=(schedule.get("media_type") == "announcement"),
                )
            elif time_diff > 300:
                # Missed by more than 5 minutes, mark as cancelled
                logger.warning(
                    f"Schedule missed, cancelling: {schedule['filename']} (was scheduled for {scheduled_dt})"
                )
                db.update_one_time_schedule_status(schedule["id"], "cancelled")

    def _check_recurring_schedules(self):
        """Check and trigger active recurring schedules."""
        now = datetime.now()
        current_day = now.weekday()  # Monday=0, Sunday=6
        current_time = now.strftime("%H:%M")

        active_schedules = db.get_active_recurring_schedules()

        for schedule in active_schedules:
            schedule_id = schedule["id"]
            days = schedule["days_of_week"]

            # Check if today is in the scheduled days
            if current_day not in days:
                continue

            should_trigger = False

            # Check specific times
            if schedule.get("specific_times"):
                for scheduled_time in schedule["specific_times"]:
                    if self._times_match(current_time, scheduled_time):
                        should_trigger = True
                        break

            # Check interval-based (between start and end time)
            elif schedule.get("interval_minutes") and schedule["interval_minutes"] > 0:
                start_time = schedule["start_time"]
                end_time = schedule.get("end_time") or "23:59"
                interval = schedule["interval_minutes"]

                if self._is_time_in_range(current_time, start_time, end_time):
                    # Check if enough time has passed since last trigger
                    last_trigger = self._last_recurring_triggers.get(schedule_id)
                    if last_trigger is None:
                        # First trigger - check if we're at a valid interval point
                        should_trigger = self._is_interval_point(
                            current_time, start_time, interval
                        )
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
                log_trigger(
                    "recurring",
                    {
                        "filename": schedule["filename"],
                        "media_type": schedule.get("media_type", "music"),
                        "schedule_id": schedule_id,
                    },
                )
                self._play_media(
                    schedule["filepath"],
                    schedule_id,
                    is_one_time=False,
                    is_announcement=(schedule.get("media_type") == "announcement"),
                )
                self._last_recurring_triggers[schedule_id] = now

    def _play_media(
        self,
        filepath: str,
        schedule_id: int,
        is_one_time: bool,
        is_announcement: bool = False,
    ):
        """Trigger media playback with announcement priority."""
        player = get_player()

        # is_announcement is now passed from caller based on media_type from database

        # If something is already playing
        if player.is_playing:
            if is_announcement:
                # For announcements: save current playlist state, stop current, play announcement
                playlist_was_active = player._playlist_active
                playlist_files = (
                    list(player._playlist) if player._playlist_active else []
                )
                playlist_index = player._playlist_index
                playlist_loop = player._playlist_loop

                logger.info(
                    f"Announcement interrupting - saving playlist state (active={playlist_was_active}, index={playlist_index})"
                )

                # Stop but don't clear playlist
                player.stop()

                # Play announcement
                success = player.play(filepath)

                if success and playlist_was_active and playlist_files:
                    # Idempotency: don't start another restore if one is already in progress
                    should_start_restore = False
                    with self._restore_lock:
                        if not self._restore_in_progress:
                            self._restore_in_progress = True
                            should_start_restore = True
                        else:
                            logger.info(
                                "Restore already in progress, skipping duplicate"
                            )

                    if should_start_restore:
                        # Wait for announcement to finish, then restore playlist
                        def restore_playlist():
                            try:
                                # Wait for current playback to finish
                                while player.is_playing:
                                    time.sleep(0.5)

                                logger.info(
                                    f"Announcement finished - resuming playlist from index {playlist_index + 1}"
                                )
                                player._playlist = playlist_files
                                player._playlist_loop = playlist_loop
                                # Resume from NEXT track (user requested this behavior)
                                next_idx = (playlist_index + 1) % len(playlist_files)
                                player._playlist_index = (
                                    next_idx - 1
                                )  # Will be incremented by play_next
                                player._playlist_active = True
                                # Sync to DB before playing
                                db.save_playlist_state(
                                    playlist=playlist_files,
                                    index=next_idx - 1,
                                    loop=playlist_loop,
                                    active=True,
                                )
                                player.play_next()
                            except Exception as e:
                                logger.error(f"Restore playlist thread error: {e}")
                            finally:
                                # Cleanup: remove from tracking and reset flag
                                with self._restore_lock:
                                    self._restore_in_progress = False
                                    if restore_thread in self._restore_threads:
                                        self._restore_threads.remove(restore_thread)

                        # Run restore in background thread with tracking
                        restore_thread = threading.Thread(
                            target=restore_playlist, daemon=True
                        )
                        with self._restore_lock:
                            self._restore_threads.append(restore_thread)
                        restore_thread.start()
            else:
                # For music: just interrupt
                logger.info("Interrupting current playback for scheduled music")
                player.stop()
                success = player.play(filepath)
        else:
            success = player.play(filepath)

        if success and is_one_time:
            db.update_one_time_schedule_status(schedule_id, "played")

    def _times_match(self, current: str, scheduled: str) -> bool:
        """Check if two HH:MM times match."""
        return current == scheduled

    def _is_time_in_range(self, current: str, start: str, end: str) -> bool:
        """Check if current time is between start and end."""
        try:
            curr = datetime.strptime(current, "%H:%M").time()
            st = datetime.strptime(start, "%H:%M").time()
            en = datetime.strptime(end, "%H:%M").time()
            return st <= curr <= en
        except ValueError:
            return False

    def _is_interval_point(self, current: str, start: str, interval: int) -> bool:
        """Check if current time is at a valid interval point from start."""
        try:
            curr = datetime.strptime(current, "%H:%M")
            st = datetime.strptime(start, "%H:%M")
            diff_minutes = (curr - st).total_seconds() / 60
            return diff_minutes >= 0 and diff_minutes % interval < 1
        except ValueError:
            return False


# Singleton instance
_scheduler_instance: Optional[Scheduler] = None


def get_scheduler() -> Scheduler:
    """Get the singleton scheduler instance."""
    global _scheduler_instance
    if _scheduler_instance is None:
        _scheduler_instance = Scheduler()
    return _scheduler_instance


if __name__ == "__main__":
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
