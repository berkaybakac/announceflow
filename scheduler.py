"""
AnnounceFlow - Scheduler Service
Handles one-time and recurring schedule checking and triggering.
"""
import logging
import os
import threading
import time
from datetime import datetime
from typing import Optional, Any

import database as db
from player import get_player
from logger import log_trigger, log_schedule, log_prayer, log_error
from services.config_service import load_config
from services.silence_policy import (
    resolve_silence_policy,
    is_within_working_hours as _policy_is_within_working_hours,
    is_prayer_time_active as _policy_is_prayer_time_active,
)
from services.stream_policy import (
    should_force_stop_stream,
    should_interrupt_for_announcement,
    should_resume_stream,
    should_skip_scheduled_music,
)
from services.stream_service import get_stream_service
from services.volume_runtime_service import get_volume_runtime_service

logger = logging.getLogger(__name__)
_volume_runtime = get_volume_runtime_service()


def _is_time_within_window(curr_time, start_time, end_time) -> bool:
    """Check if a time is within a range, including overnight windows."""
    if start_time <= end_time:
        return start_time <= curr_time <= end_time
    # Overnight window, e.g. 22:00 -> 06:00
    return curr_time >= start_time or curr_time <= end_time


def is_within_working_hours(config: dict) -> bool:
    """Backward-compatible working hours helper."""
    return _policy_is_within_working_hours(config)


def is_prayer_time_active(config: dict) -> bool:
    """Backward-compatible prayer helper."""
    return _policy_is_prayer_time_active(
        config, allow_network=True, fail_safe_on_unknown=True
    )


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
        self._pause_state_lock = threading.Lock()
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
        self._reconcile_interval_seconds: int = 60
        self._last_reconcile_monotonic: float = 0.0
        self._last_policy_fingerprint: Optional[str] = None
        self._last_stream_silence_active: bool = False
        self._stream_policy_bootstrapped: bool = False
        self._stream_resume_worker_lock = threading.Lock()
        self._stream_resume_worker_in_progress = False
        self._announcement_done: Optional[threading.Event] = None

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

    def is_running(self) -> bool:
        return bool(self._running)

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

    def _policy_log_payload(self, decision: dict[str, Any]) -> dict[str, Any]:
        return {
            "policy": decision.get("policy", "unknown"),
            "silence_active": bool(decision.get("silence_active", False)),
            "reason_code": decision.get("reason_code", "unknown"),
            "source": decision.get("source", "none"),
            "fail_safe_applied": bool(decision.get("fail_safe_applied", False)),
        }

    def _log_policy_decision_if_changed(self, decision: dict[str, Any]) -> None:
        payload = self._policy_log_payload(decision)
        fingerprint = (
            f"{payload['policy']}|{payload['silence_active']}|{payload['reason_code']}|"
            f"{payload['source']}|{payload['fail_safe_applied']}"
        )
        if fingerprint == self._last_policy_fingerprint:
            return

        self._last_policy_fingerprint = fingerprint
        policy = payload["policy"]
        if policy == "working_hours":
            log_schedule("policy_decision", payload)
        elif policy in ("prayer", "unknown"):
            log_prayer("policy_decision", payload)
        else:
            log_schedule("policy_decision", payload)

        if payload["fail_safe_applied"]:
            log_error("policy_fail_safe_engaged", payload)

    def _normalize_pause_state(self, state: Optional[dict[str, Any]]) -> Optional[dict[str, Any]]:
        if not state:
            return None
        return {
            "playlist": list(state.get("playlist") or []),
            "index": state.get("index", -1),
            "loop": state.get("loop", True),
            "active": bool(state.get("active", True)),
        }

    def _normalize_pause_policy(self, policy: str) -> str:
        return "working_hours" if policy == "working_hours" else "prayer"

    def _set_pause_state(self, policy: str, state: dict[str, Any]) -> None:
        pause_state = self._normalize_pause_state(state)
        if pause_state is None:
            return
        normalized_policy = self._normalize_pause_policy(policy)
        with self._pause_state_lock:
            if normalized_policy == "working_hours":
                self._working_hours_pause_state = pause_state
            else:
                self._prayer_pause_state = pause_state

    def _get_pause_state(self, policy: str) -> Optional[dict[str, Any]]:
        normalized_policy = self._normalize_pause_policy(policy)
        with self._pause_state_lock:
            if normalized_policy == "working_hours":
                state = self._working_hours_pause_state
            else:
                state = self._prayer_pause_state
        return self._normalize_pause_state(state)

    def _pop_pause_state(self, policy: str) -> Optional[dict[str, Any]]:
        normalized_policy = self._normalize_pause_policy(policy)
        with self._pause_state_lock:
            if normalized_policy == "working_hours":
                state = self._working_hours_pause_state
                self._working_hours_pause_state = None
            else:
                state = self._prayer_pause_state
                self._prayer_pause_state = None
        return self._normalize_pause_state(state)

    def _clear_pause_state_if_equal(self, policy: str, expected_state: dict[str, Any]) -> None:
        normalized_policy = self._normalize_pause_policy(policy)
        expected = self._normalize_pause_state(expected_state)
        with self._pause_state_lock:
            current = (
                self._working_hours_pause_state
                if normalized_policy == "working_hours"
                else self._prayer_pause_state
            )
            if current == expected:
                if normalized_policy == "working_hours":
                    self._working_hours_pause_state = None
                else:
                    self._prayer_pause_state = None

    def _move_prayer_state_to_working_hours_if_needed(self) -> None:
        with self._pause_state_lock:
            if self._prayer_pause_state is None:
                return
            if self._working_hours_pause_state is None:
                self._working_hours_pause_state = self._normalize_pause_state(
                    self._prayer_pause_state
                )
            self._prayer_pause_state = None

    def _set_pause_state_by_policy(self, state: dict[str, Any], policy: str) -> None:
        pause_state = {
            "playlist": list(state.get("playlist") or []),
            "index": state.get("index", -1),
            "loop": state.get("loop", True),
            "active": bool(state.get("active", True)),
        }
        self._set_pause_state(policy, pause_state)

    def defer_playlist_restore(self, policy: str, pause_state: dict[str, Any]) -> None:
        """Public entrypoint for startup to defer playlist restore safely."""
        self._set_pause_state(policy, pause_state)

    def has_deferred_restore(self, policy: str) -> bool:
        return self._get_pause_state(policy) is not None

    def _resume_playlist_state(self, player, state: dict[str, Any], source: str) -> bool:
        playlist = list(state.get("playlist") or [])
        if not playlist:
            return False

        if not db.get_playlist_state().get("active", False):
            return False

        index = int(state.get("index", -1))
        loop = bool(state.get("loop", True))
        next_idx = (index + 1) % len(playlist)
        logger.info(
            "Reconcile watchdog - resuming playlist (source=%s, index=%s, tracks=%s)",
            source,
            index,
            len(playlist),
        )
        success = player.apply_playlist_state(
            playlist=playlist,
            index=next_idx - 1,
            loop=loop,
            runtime_active=True,
            db_active=True,
            play_next=True,
        )
        if success:
            log_schedule(
                "reconcile_resume",
                {"source": source, "index": index, "tracks": len(playlist)},
            )
        return bool(success)

    def _run_reconcile_watchdog(
        self, config: dict, player, silence_decision: dict[str, Any]
    ) -> None:
        now_mono = time.monotonic()
        if (
            self._last_reconcile_monotonic
            and (now_mono - self._last_reconcile_monotonic)
            < self._reconcile_interval_seconds
        ):
            return
        self._last_reconcile_monotonic = now_mono

        if silence_decision.get("silence_active", False):
            if player.is_playing or player._playlist_active:
                player.stop()
            player.apply_playlist_state(runtime_active=False)
            return

        with self._restore_lock:
            if self._restore_in_progress:
                return

        if player.is_playing:
            return

        prayer_state = self._get_pause_state("prayer")
        if prayer_state and prayer_state.get("active") and prayer_state.get("playlist"):
            if self._resume_playlist_state(player, prayer_state, "prayer_pause_state"):
                self._clear_pause_state_if_equal("prayer", prayer_state)
            return

        work_state = self._get_pause_state("working_hours")
        if work_state and work_state.get("active") and work_state.get("playlist"):
            if self._resume_playlist_state(player, work_state, "working_hours_pause_state"):
                self._clear_pause_state_if_equal("working_hours", work_state)
            return

        resume_state = self._resolve_playlist_resume_state(player)
        if resume_state["active"] and resume_state["playlist"]:
            self._resume_playlist_state(player, resume_state, "db_playlist_intent")

    def _handle_prayer_time(
        self, config: dict, player, silence_decision: dict[str, Any]
    ) -> bool:
        """Handle prayer time check and playlist pause/resume.

        Returns True if we should skip the rest of the loop (prayer time active).
        """
        if not is_within_working_hours(config):
            return False

        prayer_like_silence = bool(
            silence_decision.get("silence_active", False)
            and silence_decision.get("policy") in ("prayer", "unknown")
        )

        if prayer_like_silence:
            resume_state = self._resolve_playlist_resume_state(player)

            # Save once per prayer window if there is an active loop intent.
            if self._get_pause_state("prayer") is None and resume_state["active"]:
                self._set_pause_state(
                    "prayer",
                    {
                        "playlist": resume_state["playlist"],
                        "index": resume_state["index"],
                        "loop": resume_state["loop"],
                        "active": resume_state["active"],
                    },
                )
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
                        "policy": silence_decision.get("policy", "unknown"),
                        "reason_code": silence_decision.get("reason_code", "unknown"),
                    },
                )

            # Stop only if something is actually playing.
            if player.is_playing or player._playlist_active:
                # Use stop() instead of stop_playlist() to preserve DB state
                player.stop()

            prayer_state = self._get_pause_state("prayer")
            if (
                prayer_state
                and prayer_state.get("active")
                and prayer_state.get("playlist")
            ):
                player.apply_playlist_state(
                    playlist=prayer_state["playlist"],
                    index=prayer_state["index"],
                    loop=prayer_state["loop"],
                    runtime_active=False,
                    db_active=True,
                )
            else:
                player.apply_playlist_state(runtime_active=False)
            return True  # Skip rest of loop

        # Prayer time ended - restore playlist if we saved state
        state = self._pop_pause_state("prayer")
        if state is not None:

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
            self._move_prayer_state_to_working_hours_if_needed()
            resume_state = self._resolve_playlist_resume_state(player)
            # Save playlist state BEFORE stopping (only once)
            if self._get_pause_state("working_hours") is None and resume_state["active"]:
                self._set_pause_state(
                    "working_hours",
                    {
                        "playlist": resume_state["playlist"],
                        "index": resume_state["index"],
                        "loop": resume_state["loop"],
                        "active": resume_state["active"],
                    },
                )
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

            work_state = self._get_pause_state("working_hours")
            if (
                work_state
                and work_state.get("active")
                and work_state.get("playlist")
            ):
                player.apply_playlist_state(
                    playlist=work_state["playlist"],
                    index=work_state["index"],
                    loop=work_state["loop"],
                    runtime_active=False,
                    db_active=True,
                )
            else:
                player.apply_playlist_state(runtime_active=False)
        else:
            # Working hours started - restore playlist if we saved state
            state = self._pop_pause_state("working_hours")
            if state is not None:

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

    def _apply_stream_runtime_policy(self, silence_decision: dict[str, Any]) -> None:
        """Apply Faz 4 stream runtime rules against current silence decision."""
        stream_service = get_stream_service()
        stream_status = stream_service.status()
        stream_active = bool(stream_status.get("active"))
        silence_active = bool(silence_decision.get("silence_active", False))

        if should_force_stop_stream(silence_active) and stream_active:
            stream_service.force_stop_by_policy()

        silence_ended = self._last_stream_silence_active and not silence_active
        bootstrap_resume = (
            not self._stream_policy_bootstrapped
            and not silence_active
            and stream_status.get("state") == "stopped_by_policy"
        )
        should_try_resume = silence_ended or bootstrap_resume
        if should_try_resume and stream_status.get("state") == "stopped_by_policy":
            stream_service.resume_after_policy()

        self._last_stream_silence_active = silence_active
        self._stream_policy_bootstrapped = True

    def _resume_stream_after_announcement_worker(self) -> None:
        """Resume stream after announcement playback completes."""
        try:
            player = get_player()
            done = self._announcement_done
            if done is not None:
                done.wait(timeout=120.0)
            else:
                # Fallback: no event (shouldn't happen) — bounded poll
                deadline = time.monotonic() + 120.0
                while player.is_playing and time.monotonic() < deadline:
                    time.sleep(0.2)

            stream_service = get_stream_service()
            config = self._get_cached_config()
            decision = resolve_silence_policy(
                config,
                allow_network=False,
                fail_safe_on_unknown=True,
            )
            self._log_policy_decision_if_changed(decision)
            if should_force_stop_stream(decision.get("silence_active", False)):
                stream_service.force_stop_by_policy()
                return

            sender_alive = stream_service.policy_sender_alive()
            if should_resume_stream(True, sender_alive):
                stream_service.resume_after_announcement()
        except Exception as e:
            logger.error(f"Announcement stream resume worker error: {e}")
        finally:
            with self._stream_resume_worker_lock:
                self._stream_resume_worker_in_progress = False

    def _start_stream_resume_worker_after_announcement(self) -> bool:
        with self._stream_resume_worker_lock:
            if self._stream_resume_worker_in_progress:
                logger.info(
                    "Announcement stream resume worker already running; skipping new start"
                )
                return False
            self._stream_resume_worker_in_progress = True

        try:
            thread = threading.Thread(
                target=self._resume_stream_after_announcement_worker,
                daemon=True,
            )
            thread.start()
            return True
        except Exception:
            with self._stream_resume_worker_lock:
                self._stream_resume_worker_in_progress = False
            raise

    def _run_loop(self):
        """Main scheduler loop."""
        while self._running:
            try:
                config = self._get_cached_config()
                player = get_player()
                silence_decision = resolve_silence_policy(
                    config,
                    allow_network=True,
                    fail_safe_on_unknown=True,
                )
                self._log_policy_decision_if_changed(silence_decision)
                self._apply_stream_runtime_policy(silence_decision)

                # 1. Prayer time check - highest priority, stops EVERYTHING
                prayer_pause_active = self._handle_prayer_time(
                    config, player, silence_decision
                )

                # 2. Working hours check
                outside_working_hours = False
                if not prayer_pause_active:
                    outside_working_hours = self._handle_working_hours(config, player)

                # 3. One-time schedules: check always (outside hours => cancel if due)
                if not prayer_pause_active:
                    self._check_one_time_schedules(outside_working_hours)

                # 4. Only check recurring schedules during working hours
                if not prayer_pause_active and not outside_working_hours:
                    self._check_recurring_schedules()

                self._run_reconcile_watchdog(config, player, silence_decision)

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

    def _capture_restore_snapshot(self, player) -> dict[str, Any]:
        resume_state = self._resolve_playlist_resume_state(player)
        playlist_was_active = resume_state["active"]
        playlist_files = list(resume_state["playlist"]) if playlist_was_active else []
        snapshot = {
            "playlist_was_active": playlist_was_active,
            "playlist_files": playlist_files,
            "playlist_index": resume_state["index"],
            "playlist_loop": resume_state["loop"],
        }
        if playlist_was_active and playlist_files:
            db.save_playlist_state(
                playlist=playlist_files,
                index=snapshot["playlist_index"],
                loop=snapshot["playlist_loop"],
                active=True,
            )
        return snapshot

    def _interrupt_for_scheduled_media(
        self, player, is_announcement: bool, snapshot: dict[str, Any]
    ) -> None:
        if not player.is_playing:
            return

        if is_announcement:
            logger.info(
                "Announcement interrupting - saving playlist state "
                f"(active={snapshot['playlist_was_active']}, index={snapshot['playlist_index']})"
            )
        else:
            logger.info("Interrupting current playback for scheduled music")
        player.stop()
        if snapshot["playlist_was_active"] and snapshot["playlist_files"]:
            db.save_playlist_state(
                playlist=snapshot["playlist_files"],
                index=snapshot["playlist_index"],
                loop=snapshot["playlist_loop"],
                active=True,
            )

    def _start_scheduled_media(self, player, filepath: str, preserve_playlist: bool) -> bool:
        return bool(player.play(filepath, preserve_playlist=preserve_playlist))

    def _queue_restore_target(self, snapshot: dict[str, Any]) -> bool:
        if not snapshot["playlist_was_active"] or not snapshot["playlist_files"]:
            return False

        restore_state = {
            "playlist": list(snapshot["playlist_files"]),
            "index": snapshot["playlist_index"],
            "loop": snapshot["playlist_loop"],
            "active": True,
        }

        should_start_restore = False
        with self._restore_lock:
            self._restore_target_state = restore_state
            if not self._restore_in_progress:
                self._restore_in_progress = True
                should_start_restore = True
            else:
                logger.info("Restore already in progress, updated restore target")
        return should_start_restore

    def _restore_worker_once(self, player, state: dict[str, Any]) -> bool:
        if not db.get_playlist_state().get("active", False):
            return False

        playlist = state.get("playlist") or []
        index = state.get("index", -1)
        loop = state.get("loop", True)
        if not playlist:
            return False

        resume_config = self._get_cached_config()
        restore_decision = resolve_silence_policy(
            resume_config,
            allow_network=False,
            fail_safe_on_unknown=True,
        )
        self._log_policy_decision_if_changed(restore_decision)
        if restore_decision.get("silence_active", False):
            policy = (
                "working_hours"
                if restore_decision.get("policy") == "working_hours"
                else "prayer"
            )
            self._set_pause_state(
                policy,
                {
                    "playlist": playlist,
                    "index": index,
                    "loop": loop,
                    "active": True,
                },
            )
            return False

        logger.info(
            f"Scheduled media finished - resuming playlist from index {index + 1}"
        )
        next_idx = (index + 1) % len(playlist)
        player.apply_playlist_state(
            playlist=playlist,
            index=next_idx - 1,
            loop=loop,
            runtime_active=True,
            db_active=True,
            play_next=True,
        )
        return True

    def _run_restore_worker(self, player) -> None:
        try:
            while True:
                while player.is_playing:
                    time.sleep(0.5)

                done = self._announcement_done
                if done is not None and not done.is_set():
                    done.set()
                    self._announcement_done = None

                with self._restore_lock:
                    state = self._restore_target_state
                    self._restore_target_state = None

                if not state:
                    break

                if not self._restore_worker_once(player, state):
                    break
        except Exception as e:
            logger.error(f"Restore playlist thread error: {e}")
        finally:
            current_thread = threading.current_thread()
            with self._restore_lock:
                self._restore_in_progress = False
                if current_thread in self._restore_threads:
                    self._restore_threads.remove(current_thread)

    def _start_restore_worker(self, player) -> None:
        restore_thread = threading.Thread(
            target=self._run_restore_worker, args=(player,), daemon=True
        )
        with self._restore_lock:
            self._restore_threads.append(restore_thread)
        restore_thread.start()

    def _finalize_one_time_status(
        self, success: bool, schedule_id: int, is_one_time: bool
    ) -> None:
        if success and is_one_time:
            db.update_one_time_schedule_status(schedule_id, "played")

    def _play_media(
        self,
        filepath: str,
        schedule_id: int,
        is_one_time: bool,
        is_announcement: bool = False,
    ):
        """Trigger media playback with announcement priority."""
        player = get_player()
        stream_service = get_stream_service()
        config = self._get_cached_config()
        source_type = "one-time" if is_one_time else "recurring"

        policy_decision = resolve_silence_policy(
            config,
            allow_network=False,
            fail_safe_on_unknown=True,
        )
        if policy_decision.get("silence_active", False):
            logger.info(
                "Skipping scheduled media due to silence policy "
                f"(schedule_id={schedule_id}, policy={policy_decision.get('policy')}, "
                f"reason={policy_decision.get('reason_code')})"
            )
            if is_one_time:
                db.update_one_time_schedule_status(schedule_id, "cancelled")
            return

        if not is_within_working_hours(config):
            return

        stream_status = stream_service.status()
        stream_active = bool(stream_status.get("active"))

        if not is_announcement and should_skip_scheduled_music(stream_active):
            logger.info(
                "Skipping scheduled music while stream is active "
                f"(schedule_id={schedule_id})"
            )
            if is_one_time:
                db.update_one_time_schedule_status(schedule_id, "cancelled")
            return

        announcement_interrupted_stream = False
        if is_announcement and should_interrupt_for_announcement(stream_active):
            stream_service.pause_for_announcement()
            announcement_interrupted_stream = True

        snapshot = self._capture_restore_snapshot(player)
        self._interrupt_for_scheduled_media(player, is_announcement, snapshot)

        override_applied = False
        override_volume = 0
        canonical_volume = db.get_volume_state()
        if is_announcement and bool(canonical_volume.get("muted", False)):
            override_volume = max(
                1,
                int(canonical_volume.get("last_nonzero_volume", 80)),
            )
            override_applied = bool(player.set_volume(override_volume))
            if not override_applied:
                logger.warning(
                    "Scheduled announcement mute override could not set player volume"
                )

        logger.info(
            f"[source] {source_type} play -> {os.path.basename(filepath)} (schedule_id={schedule_id})"
        )
        success = self._start_scheduled_media(
            player, filepath, snapshot["playlist_was_active"]
        )
        if success and override_applied:
            _volume_runtime.activate_announcement_override(
                playback_session=getattr(player, "_playback_session", None),
                effective_volume=override_volume,
                source="scheduled_announcement",
            )
        elif not success and override_applied:
            player.set_volume(int(canonical_volume.get("volume", 0)))
        if announcement_interrupted_stream:
            self._announcement_done = threading.Event()
        restore_queued = success and self._queue_restore_target(snapshot)
        if restore_queued:
            self._start_restore_worker(player)
        elif announcement_interrupted_stream:
            # No restore worker to signal the event — start a lightweight sentinel
            _evt = self._announcement_done
            def _signal_on_player_stop():
                _p = get_player()
                deadline = time.monotonic() + 120.0
                while _p.is_playing and time.monotonic() < deadline:
                    time.sleep(0.2)
                _evt.set()
            threading.Thread(target=_signal_on_player_stop, daemon=True).start()
        if announcement_interrupted_stream:
            self._start_stream_resume_worker_after_announcement()
        self._finalize_one_time_status(success, schedule_id, is_one_time)

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
