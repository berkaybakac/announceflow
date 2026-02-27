"""
AnnounceFlow - Scheduler Service
Handles one-time and recurring schedule checking and triggering.
"""
import logging
import os
import threading
import time
from datetime import datetime
from typing import Optional

import database as db
from player import get_player
from logger import log_trigger, log_schedule, log_prayer, log_error
from services.config_service import load_config

logger = logging.getLogger(__name__)


def _is_time_within_window(curr_time, start_time, end_time) -> bool:
    """Check if a time is within a range, including overnight windows."""
    if start_time <= end_time:
        return start_time <= curr_time <= end_time
    # Overnight window, e.g. 22:00 -> 06:00
    return curr_time >= start_time or curr_time <= end_time


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

        return _is_time_within_window(curr, start, end)
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
        self._restore_target_state: Optional[dict] = None
        # Config caching to reduce disk I/O
        self._config_cache: Optional[dict] = None
        self._config_cache_time: float = 0
        self._config_cache_ttl: int = 10  # Reload config every 10 seconds max

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

    def _resolve_playlist_resume_state(self, player) -> dict:
        """Resolve effective playlist state using both memory and DB intent.

        During scheduled interruptions, ``player.stop()`` intentionally marks
        ``player._playlist_active`` false to avoid monitor races. The persisted
        DB flag still carries resume intent, so we merge both sources here.
        """
        db_state = db.get_playlist_state()
        db_playlist = list(db_state.get("playlist") or [])
        db_index = db_state.get("index", -1)
        db_loop = db_state.get("loop", True)
        db_active = bool(db_state.get("active", False))

        memory_playlist = list(player._playlist) if player._playlist else []
        has_memory_playlist = len(memory_playlist) > 0

        playlist = memory_playlist if has_memory_playlist else db_playlist
        index = player._playlist_index if has_memory_playlist else db_index
        loop = player._playlist_loop if has_memory_playlist else db_loop

        memory_active = bool(player._playlist_active and has_memory_playlist)
        active = bool((memory_active or db_active) and playlist)

        return {
            "playlist": playlist,
            "index": -1 if index is None else index,
            "loop": bool(loop),
            "active": active,
            "db_active": db_active,
            "memory_active": memory_active,
        }

    def _handle_prayer_time(self, config: dict, player) -> bool:
        """Handle prayer time check and playlist pause/resume.

        Returns True if we should skip the rest of the loop (prayer time active).
        """
        if not is_within_working_hours(config):
            return False

        if is_prayer_time_active(config):
            resume_state = self._resolve_playlist_resume_state(player)

            # Save once per prayer window if there is an active loop intent.
            if self._prayer_pause_state is None and resume_state["active"]:
                self._prayer_pause_state = {
                    "playlist": resume_state["playlist"],
                    "index": resume_state["index"],
                    "loop": resume_state["loop"],
                    "active": resume_state["active"],
                }
                logger.info(
                    "Prayer time - saving playlist state "
                    f"(index={resume_state['index']}, tracks={len(resume_state['playlist'])}, "
                    f"active={resume_state['active']})"
                )
                log_prayer(
                    "silence_start",
                    {
                        "index": resume_state["index"],
                        "tracks": len(resume_state["playlist"]),
                    },
                )

            # Stop only if something is actually playing.
            if player.is_playing or player._playlist_active:
                # Use stop() instead of stop_playlist() to preserve DB state
                player.stop()

            if (
                self._prayer_pause_state
                and self._prayer_pause_state.get("active")
                and self._prayer_pause_state.get("playlist")
            ):
                player.apply_playlist_state(
                    playlist=self._prayer_pause_state["playlist"],
                    index=self._prayer_pause_state["index"],
                    loop=self._prayer_pause_state["loop"],
                    runtime_active=False,
                    db_active=True,
                )
            else:
                player.apply_playlist_state(runtime_active=False)
            return True  # Skip rest of loop

        # Prayer time ended - restore playlist if we saved state
        if self._prayer_pause_state is not None:
            state = self._prayer_pause_state
            self._prayer_pause_state = None

            if state["active"] and state["playlist"]:
                if not db.get_playlist_state().get("active", False):
                    logger.info(
                        "Prayer ended - playlist resume cancelled (DB marked inactive)"
                    )
                    return False
                logger.info(
                    f"Prayer ended - restoring playlist (index={state['index']})"
                )
                log_prayer("silence_end", {"index": state["index"]})
                player.apply_playlist_state(
                    playlist=state["playlist"],
                    index=state["index"],
                    loop=state["loop"],
                    runtime_active=True,
                    db_active=True,
                    play_next=True,
                )

        return False  # Continue with rest of loop

    def _handle_working_hours(self, config: dict, player) -> bool:
        """Handle working hours check and playlist pause/resume.

        Returns True if outside working hours (schedules should be limited).
        """
        outside_working_hours = not is_within_working_hours(config)

        if outside_working_hours:
            if self._prayer_pause_state is not None:
                if self._working_hours_pause_state is None:
                    self._working_hours_pause_state = self._prayer_pause_state
                self._prayer_pause_state = None
            resume_state = self._resolve_playlist_resume_state(player)
            # Save playlist state BEFORE stopping (only once)
            if self._working_hours_pause_state is None and resume_state["active"]:
                self._working_hours_pause_state = {
                    "playlist": resume_state["playlist"],
                    "index": resume_state["index"],
                    "loop": resume_state["loop"],
                    "active": resume_state["active"],
                }
                logger.info(
                    "Outside working hours - saving playlist state "
                    f"(index={resume_state['index']}, tracks={len(resume_state['playlist'])}, "
                    f"active={resume_state['active']})"
                )
                log_schedule(
                    "working_hours_end",
                    {
                        "index": resume_state["index"],
                        "tracks": len(resume_state["playlist"]),
                    },
                )

            # Use stop() instead of stop_playlist() to preserve DB state
            if player.is_playing or player._playlist_active:
                player.stop()

            if (
                self._working_hours_pause_state
                and self._working_hours_pause_state.get("active")
                and self._working_hours_pause_state.get("playlist")
            ):
                player.apply_playlist_state(
                    playlist=self._working_hours_pause_state["playlist"],
                    index=self._working_hours_pause_state["index"],
                    loop=self._working_hours_pause_state["loop"],
                    runtime_active=False,
                    db_active=True,
                )
            else:
                player.apply_playlist_state(runtime_active=False)
        else:
            # Working hours started - restore playlist if we saved state
            if self._working_hours_pause_state is not None:
                state = self._working_hours_pause_state
                self._working_hours_pause_state = None

                if state["active"] and state["playlist"]:
                    if not db.get_playlist_state().get("active", False):
                        logger.info(
                            "Working hours started - resume cancelled (DB marked inactive)"
                        )
                        return outside_working_hours
                    logger.info(
                        f"Working hours started - restoring playlist (index={state['index']})"
                    )
                    log_schedule("working_hours_start", {"index": state["index"]})
                    player.apply_playlist_state(
                        playlist=state["playlist"],
                        index=state["index"],
                        loop=state["loop"],
                        runtime_active=True,
                        db_active=True,
                        play_next=True,
                    )

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

                # 3. One-time schedules: check always (outside hours => cancel if due)
                self._check_one_time_schedules(outside_working_hours)

                # 4. Only check recurring schedules during working hours
                if not outside_working_hours:
                    self._check_recurring_schedules()

            except Exception as e:
                logger.error(f"Scheduler error: {e}")

            time.sleep(self.check_interval)

    def _check_one_time_schedules(self, outside_working_hours: bool):
        """Check and trigger pending one-time schedules."""
        now = datetime.now()
        pending = db.get_pending_one_time_schedules()

        for schedule in pending:
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
                if outside_working_hours:
                    logger.warning(
                        f"Schedule outside working hours, cancelling: {schedule['filename']} (was scheduled for {scheduled_dt})"
                    )
                    db.update_one_time_schedule_status(schedule["id"], "cancelled")
                else:
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
                        elapsed_seconds = (now - last_trigger).total_seconds()
                        # Keep tolerance small (scheduler tick scale), not minute scale.
                        # Minute-scale tolerance causes "2 min" jobs to run every ~1 min.
                        tolerance_seconds = max(2, int(self.check_interval) + 2)
                        if elapsed_seconds >= (interval * 60) - tolerance_seconds:
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
        config = self._get_cached_config()
        source_type = "one-time" if is_one_time else "recurring"

        if not is_within_working_hours(config):
            return

        # is_announcement is now passed from caller based on media_type from database
        resume_state = self._resolve_playlist_resume_state(player)
        playlist_was_active = resume_state["active"]
        playlist_files = list(resume_state["playlist"]) if playlist_was_active else []
        playlist_index = resume_state["index"]
        playlist_loop = resume_state["loop"]

        # Keep loop intent alive across scheduled interruptions unless user explicitly
        # pressed Stop.
        if playlist_was_active and playlist_files:
            db.save_playlist_state(
                playlist=playlist_files,
                index=playlist_index,
                loop=playlist_loop,
                active=True,
            )

        if player.is_playing:
            if is_announcement:
                logger.info(
                    f"Announcement interrupting - saving playlist state (active={playlist_was_active}, index={playlist_index})"
                )
            else:
                logger.info("Interrupting current playback for scheduled music")
            player.stop()
            if playlist_was_active and playlist_files:
                # stop() marks playlist inactive in DB; restore intent for post-scheduled resume
                db.save_playlist_state(
                    playlist=playlist_files,
                    index=playlist_index,
                    loop=playlist_loop,
                    active=True,
                )

        logger.info(
            f"[source] {source_type} play -> {os.path.basename(filepath)} (schedule_id={schedule_id})"
        )
        success = player.play(filepath, preserve_playlist=playlist_was_active)

        if success and playlist_was_active and playlist_files:
            restore_state = {
                "playlist": list(playlist_files),
                "index": playlist_index,
                "loop": playlist_loop,
                "active": True,
            }

            # Keep latest restore target; if a restore worker is already running,
            # it will consume this updated state after current playback ends.
            should_start_restore = False
            with self._restore_lock:
                self._restore_target_state = restore_state
                if not self._restore_in_progress:
                    self._restore_in_progress = True
                    should_start_restore = True
                else:
                    logger.info("Restore already in progress, updated restore target")

            if should_start_restore:
                # Wait for scheduled playback to finish, then restore playlist
                def restore_playlist():
                    try:
                        while True:
                            # Wait for current playback to finish
                            while player.is_playing:
                                time.sleep(0.5)

                            with self._restore_lock:
                                state = self._restore_target_state
                                self._restore_target_state = None

                            if not state:
                                break

                            if not db.get_playlist_state().get("active", False):
                                break

                            playlist = state.get("playlist") or []
                            index = state.get("index", -1)
                            loop = state.get("loop", True)
                            if not playlist:
                                break

                            resume_config = self._get_cached_config()
                            if not is_within_working_hours(resume_config):
                                self._working_hours_pause_state = {
                                    "playlist": list(playlist),
                                    "index": index,
                                    "loop": loop,
                                    "active": True,
                                }
                                break

                            logger.info(
                                f"Scheduled media finished - resuming playlist from index {index + 1}"
                            )
                            # Resume from NEXT track (user requested this behavior)
                            next_idx = (index + 1) % len(playlist)
                            player.apply_playlist_state(
                                playlist=playlist,
                                index=next_idx - 1,
                                loop=loop,
                                runtime_active=True,
                                db_active=True,
                                play_next=True,
                            )
                    except Exception as e:
                        logger.error(f"Restore playlist thread error: {e}")
                    finally:
                        # Cleanup: remove from tracking and reset flag
                        with self._restore_lock:
                            self._restore_in_progress = False
                            if restore_thread in self._restore_threads:
                                self._restore_threads.remove(restore_thread)

                # Run restore in background thread with tracking
                restore_thread = threading.Thread(target=restore_playlist, daemon=True)
                with self._restore_lock:
                    self._restore_threads.append(restore_thread)
                restore_thread.start()

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
            return _is_time_within_window(curr, st, en)
        except ValueError:
            return False

    def _is_interval_point(self, current: str, start: str, interval: int) -> bool:
        """Check if current time is at a valid interval point from start."""
        try:
            curr_time = datetime.strptime(current, "%H:%M").time()
            start_time = datetime.strptime(start, "%H:%M").time()

            curr_minutes = curr_time.hour * 60 + curr_time.minute
            start_minutes = start_time.hour * 60 + start_time.minute

            # Wrap across midnight for overnight schedules (e.g. 22:00 -> 06:00).
            # For same-day windows this remains equivalent to simple subtraction.
            diff_minutes = (curr_minutes - start_minutes) % (24 * 60)
            return diff_minutes % interval == 0
        except ValueError:
            return False


# Singleton instance
_scheduler_instance: Optional[Scheduler] = None


def _resolve_scheduler_interval_seconds() -> int:
    """Resolve scheduler loop interval from config safely."""
    config = load_config()
    raw = config.get("scheduler_interval_seconds", 10)
    try:
        value = int(raw)
        if value < 1:
            raise ValueError("must be >= 1")
        return value
    except (TypeError, ValueError):
        logger.warning(
            f"Invalid scheduler_interval_seconds={raw!r}; falling back to 10 seconds"
        )
        return 10


def get_scheduler() -> Scheduler:
    """Get the singleton scheduler instance."""
    global _scheduler_instance
    if _scheduler_instance is None:
        _scheduler_instance = Scheduler(
            check_interval_seconds=_resolve_scheduler_interval_seconds()
        )
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
