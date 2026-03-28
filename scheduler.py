"""
AnnounceFlow - Scheduler Service
Handles one-time and recurring schedule checking and triggering.
"""
import logging
import os
import threading
import time
from collections import deque
from datetime import datetime
from typing import Optional, Any

import database as db
from player import get_player
from logger import log_trigger, log_schedule, log_prayer, log_error
from services.config_service import load_config
from services.schedule_conflict_service import resolve_duration_seconds
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
from utils.time_utils import (
    now_local,
    now_utc,
    parse_storage_datetime_to_utc,
    to_storage_utc_z,
)

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
        # Queue-Lite (announcement only): resilient, ordered dispatch under policy blocks.
        # Lock protects all queue state reads/writes against concurrent Flask threads.
        self._queue_lock = threading.Lock()
        self._announcement_queue = deque()
        self._announcement_enqueued_keys: set[str] = set()
        self._queued_one_time_ids: set[int] = set()
        self._announcement_current: Optional[dict[str, Any]] = None
        self._announcement_gap_seconds: int = 10
        self._announcement_max_delay_seconds: int = 15 * 60
        self._announcement_next_allowed_monotonic: float = 0.0
        self._announcement_last_block_reason: Optional[str] = None
        self._announcement_queue_counters: dict[str, int] = {
            "dropped_stale": 0,
            "dropped_invalid": 0,
            "stuck_reset": 0,
        }
        self._announcement_health_log_interval_seconds: int = 60
        self._announcement_last_health_log_monotonic: float = 0.0
        self._announcement_enqueue_seq: int = 0
        self._media_type_audited: bool = False
        # Per-tick guard: at most one _play_media call succeeds per scheduler tick.
        self._tick_media_dispatched: bool = False

    def start(self):
        """Start the scheduler background thread."""
        if self._running:
            logger.warning("Scheduler already running")
            return
        self._audit_media_types_once()

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

    def get_announcement_queue_status(self) -> dict[str, int]:
        """Return lightweight Queue-Lite stats for UI visibility.

        Called from Flask request threads — lock ensures a consistent snapshot.
        """
        with self._queue_lock:
            return {
                "queued": len(self._announcement_queue),
                "active": 1 if self._announcement_current is not None else 0,
                "dropped_stale": int(self._announcement_queue_counters.get("dropped_stale", 0)),
                "dropped_invalid": int(self._announcement_queue_counters.get("dropped_invalid", 0)),
                "stuck_reset": int(self._announcement_queue_counters.get("stuck_reset", 0)),
            }

    def _normalize_media_type(self, raw_media_type: Any) -> str:
        normalized = str(raw_media_type or "").strip().lower()
        if normalized == "announcement":
            return "announcement"
        return "music"

    def _is_announcement_media_type(self, raw_media_type: Any) -> bool:
        return self._normalize_media_type(raw_media_type) == "announcement"

    def _audit_media_types_once(self) -> None:
        """Normalize legacy/invalid media_type rows at startup."""
        if self._media_type_audited:
            return

        conn = None
        try:
            conn = db.get_db_connection()
            cur = conn.cursor()
            cur.execute(
                """
                UPDATE media_files
                SET media_type = 'announcement'
                WHERE lower(trim(COALESCE(media_type, ''))) = 'announcement'
            """
            )
            normalized_announcement = max(0, int(cur.rowcount or 0))
            cur.execute(
                """
                UPDATE media_files
                SET media_type = 'music'
                WHERE lower(trim(COALESCE(media_type, ''))) = 'music'
            """
            )
            normalized_music = max(0, int(cur.rowcount or 0))
            cur.execute(
                """
                UPDATE media_files
                SET media_type = 'music'
                WHERE media_type IS NULL
                   OR trim(media_type) = ''
                   OR lower(trim(media_type)) NOT IN ('music', 'announcement')
            """
            )
            coerced_invalid = max(0, int(cur.rowcount or 0))
            conn.commit()

            total = normalized_announcement + normalized_music + coerced_invalid
            if total > 0:
                log_schedule(
                    "media_type_audit_normalized",
                    {
                        "rows_total": total,
                        "rows_announcement": normalized_announcement,
                        "rows_music": normalized_music,
                        "rows_invalid_to_music": coerced_invalid,
                    },
                )
        except Exception as e:
            logger.warning("Media type startup audit failed: %s", e)
        finally:
            if conn is not None:
                conn.close()
            self._media_type_audited = True

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
        """Transfer prayer pause state into the working-hours slot when mesai ends mid-prayer.

        OVERLAP SCENARIO (known edge case — Backlog: prayer_working_hours_overlap_v2):
        ─────────────────────────────────────────────────────────────────────────────
        Triggered when working hours end while a prayer pause is already active.
        Example: akşam ezanı 16:55, mesai 17:00 bitiyor.

        Correct behaviour:
            1. Prayer state is moved to working_hours slot.
            2. Music does NOT resume at prayer end (17:07) — it would be wrong, mesai over.
            3. Music resumes the NEXT day at mesai start (09:00). ✓

        Edge case — working_hours state already set (two overlapping pauses):
            If working_hours_pause_state is not None (mesai ended BEFORE prayer started,
            e.g. mesai 16:00, prayer 16:55), prayer state OVERWRITES working_hours state.
            Prayer state is always more recent (working_hours handler is skipped during
            prayer). Music resumes at mesai start from the prayer position. ✓

        HOW TO DIAGNOSE from logs:
            • "prayer_state_moved_to_working_hours"  → normal overlap, all fine.
            • "prayer_state_overwrote_working_hours" → edge case, prayer position used.
        """
        with self._pause_state_lock:
            if self._prayer_pause_state is None:
                return

            prayer_index = self._prayer_pause_state.get("index")
            prayer_tracks = len(self._prayer_pause_state.get("playlist") or [])

            if self._working_hours_pause_state is None:
                # Normal overlap path: move prayer state so music resumes at mesai start.
                self._working_hours_pause_state = self._normalize_pause_state(
                    self._prayer_pause_state
                )
                log_prayer(
                    "prayer_state_moved_to_working_hours",
                    {
                        "index": prayer_index,
                        "tracks": prayer_tracks,
                        "reason": "working_hours_ended_during_prayer",
                        "note": "Music will resume at next mesai start, NOT at prayer end.",
                    },
                )
                logger.info(
                    "Prayer↔WorkingHours overlap: prayer state moved to working_hours slot "
                    "(index=%s, tracks=%s). Music resumes at mesai start.",
                    prayer_index,
                    prayer_tracks,
                )
            else:
                # working_hours state already exists (mesai ended BEFORE prayer started,
                # e.g. mesai 16:00, prayer 16:55). Prayer state is always more recent
                # because _handle_working_hours() is skipped while prayer is active —
                # so working_hours slot can only have been set before prayer began.
                # Overwrite with prayer state so music resumes from the correct position.
                wh_index = self._working_hours_pause_state.get("index")
                wh_tracks = len(self._working_hours_pause_state.get("playlist") or [])
                self._working_hours_pause_state = self._normalize_pause_state(
                    self._prayer_pause_state
                )
                log_prayer(
                    "prayer_state_overwrote_working_hours",
                    {
                        "prayer_index": prayer_index,
                        "prayer_tracks": prayer_tracks,
                        "replaced_working_hours_index": wh_index,
                        "replaced_working_hours_tracks": wh_tracks,
                        "note": "Music will resume at mesai start from prayer position (more recent).",
                    },
                )
                logger.info(
                    "Prayer↔WorkingHours overlap — prayer state OVERWRITES working_hours slot "
                    "(prayer index=%s replaces stale wh index=%s). "
                    "Music resumes at mesai start from prayer position.",
                    prayer_index,
                    wh_index,
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

    def _refresh_announcement_queue_runtime(self, config: dict[str, Any]) -> None:
        """Refresh Queue-Lite settings from config with safe defaults."""
        try:
            gap = int(config.get("announcement_queue_gap_seconds", 10))
        except (TypeError, ValueError):
            gap = 10
        try:
            max_delay = int(config.get("announcement_queue_max_delay_seconds", 15 * 60))
        except (TypeError, ValueError):
            max_delay = 15 * 60

        self._announcement_gap_seconds = max(0, gap)
        self._announcement_max_delay_seconds = max(60, max_delay)

    def _increment_queue_counter(self, key: str, amount: int = 1) -> None:
        self._announcement_queue_counters[key] = int(
            self._announcement_queue_counters.get(key, 0)
        ) + int(amount)

    def _cleanup_queue_item_tracking(
        self,
        item: dict[str, Any],
        *,
        remove_dedupe: bool,
        cancel_one_time: bool,
    ) -> None:
        dedupe_key = item.get("dedupe_key")
        if remove_dedupe and dedupe_key:
            self._announcement_enqueued_keys.discard(str(dedupe_key))

        schedule_id = int(item.get("schedule_id") or 0)
        is_one_time = bool(item.get("is_one_time"))
        if is_one_time and schedule_id > 0:
            self._queued_one_time_ids.discard(schedule_id)
            if cancel_one_time:
                try:
                    db.update_one_time_schedule_status(schedule_id, "cancelled")
                except Exception as exc:
                    logger.exception(
                        "Failed to cancel one-time schedule in queue cleanup id=%s",
                        schedule_id,
                    )
                    log_error(
                        "announcement_queue_status_write_failed",
                        {
                            "action": "cancelled",
                            "schedule_id": schedule_id,
                            "error": str(exc),
                        },
                    )

    def _is_one_time_schedule_dispatchable(self, schedule_id: int) -> bool:
        """Check if a one-time schedule is still valid for dispatch (pending or queued)."""
        try:
            schedule = db.get_one_time_schedule(schedule_id)
        except Exception as exc:
            logger.exception(
                "Failed to read one-time schedule during queue dispatch check id=%s",
                schedule_id,
            )
            log_error(
                "announcement_queue_status_read_failed",
                {"schedule_id": schedule_id, "error": str(exc)},
            )
            # Keep item in queue if DB read is temporarily unavailable.
            return True
        if not schedule:
            return False
        status = str(schedule.get("status") or "").strip().lower()
        return status in ("pending", "queued")

    def _drop_invalid_front_queue_items(self) -> None:
        """Drop queued one-time announcements that are no longer pending."""
        while self._announcement_queue:
            item = self._announcement_queue[0]
            if not bool(item.get("is_one_time")):
                return

            schedule_id = int(item.get("schedule_id") or 0)
            if schedule_id > 0 and self._is_one_time_schedule_dispatchable(schedule_id):
                return

            item = self._announcement_queue.popleft()
            self._cleanup_queue_item_tracking(
                item, remove_dedupe=True, cancel_one_time=False
            )
            self._increment_queue_counter("dropped_invalid")
            log_schedule(
                "announcement_queue_dropped_invalid",
                {
                    "schedule_id": schedule_id,
                    "source": item.get("source", "unknown"),
                    "queue_size": len(self._announcement_queue),
                },
            )

    def _get_stall_timeout_seconds(self, item: dict[str, Any]) -> int:
        duration_seconds = int(item.get("expected_duration_seconds") or 0)
        return max(120, duration_seconds + 30)

    def _reset_stuck_current_announcement_if_needed(self) -> None:
        current = self._announcement_current
        if not current:
            return

        started_ts = float(current.get("started_ts") or 0.0)
        if started_ts <= 0:
            return

        age_seconds = max(0, int(time.time() - started_ts))
        timeout_seconds = self._get_stall_timeout_seconds(current)
        if age_seconds <= timeout_seconds:
            return

        player = get_player()
        was_playing = bool(player.is_playing)
        if was_playing:
            player.stop()

        schedule_id = int(current.get("schedule_id") or 0)
        is_one_time = bool(current.get("is_one_time"))
        self._cleanup_queue_item_tracking(
            current, remove_dedupe=False, cancel_one_time=is_one_time
        )
        self._announcement_current = None
        self._announcement_next_allowed_monotonic = (
            time.monotonic() + float(self._announcement_gap_seconds)
        )
        self._announcement_last_block_reason = None
        self._increment_queue_counter("stuck_reset")
        log_schedule(
            "announcement_queue_stuck_reset",
            {
                "schedule_id": schedule_id,
                "is_one_time": is_one_time,
                "age_seconds": age_seconds,
                "timeout_seconds": timeout_seconds,
                "was_playing": was_playing,
            },
        )

    def _log_announcement_queue_health(self) -> None:
        now_mono = time.monotonic()
        if (
            self._announcement_last_health_log_monotonic
            and (now_mono - self._announcement_last_health_log_monotonic)
            < self._announcement_health_log_interval_seconds
        ):
            return
        self._announcement_last_health_log_monotonic = now_mono
        with self._queue_lock:
            snapshot = {
                "queue_size": len(self._announcement_queue),
                "active": bool(self._announcement_current is not None),
                "blocked_reason": self._announcement_last_block_reason or "none",
                "dropped_stale": int(
                    self._announcement_queue_counters.get("dropped_stale", 0)
                ),
                "dropped_invalid": int(
                    self._announcement_queue_counters.get("dropped_invalid", 0)
                ),
                "stuck_reset": int(
                    self._announcement_queue_counters.get("stuck_reset", 0)
                ),
            }
        log_schedule("announcement_queue_health", snapshot)

    def _queue_announcement(
        self,
        *,
        filepath: str,
        schedule_id: int,
        is_one_time: bool,
        due_dt: datetime,
        source: str,
        duration_seconds: int,
    ) -> bool:
        """Queue announcement trigger using dedupe key schedule_id + due minute."""
        due_utc = parse_storage_datetime_to_utc(due_dt, naive_as_local=True) or now_utc()
        due_minute = due_utc.strftime("%Y-%m-%d %H:%M")
        dedupe_key = f"{source}:{schedule_id}:{due_minute}"

        with self._queue_lock:
            if dedupe_key in self._announcement_enqueued_keys:
                return False
            if (
                self._announcement_current is not None
                and self._announcement_current.get("dedupe_key") == dedupe_key
            ):
                return False

            now_ts = time.time()
            self._announcement_enqueue_seq += 1
            enqueue_seq = self._announcement_enqueue_seq
            item = {
                "dedupe_key": dedupe_key,
                "filepath": filepath,
                "schedule_id": int(schedule_id),
                "is_one_time": bool(is_one_time),
                "due_dt": due_utc,
                "due_utc": to_storage_utc_z(due_utc),
                "due_ts": due_utc.timestamp(),
                "enqueued_ts": now_ts,
                "enqueue_seq": enqueue_seq,
                "source": source,
                "expected_duration_seconds": max(0, int(duration_seconds or 0)),
            }
            self._announcement_queue.append(item)
            # Deterministic FIFO: due time first, then enqueue sequence.
            self._announcement_queue = deque(
                sorted(
                    self._announcement_queue,
                    key=lambda queued_item: (
                        float(queued_item.get("due_ts") or 0.0),
                        int(queued_item.get("enqueue_seq") or 0),
                    ),
                )
            )
            self._announcement_enqueued_keys.add(dedupe_key)
            if is_one_time:
                self._queued_one_time_ids.add(int(schedule_id))
                try:
                    db.update_one_time_schedule_status(int(schedule_id), "queued")
                except Exception as exc:
                    logger.exception(
                        "Failed to set one-time schedule queued id=%s",
                        schedule_id,
                    )
                    log_error(
                        "announcement_queue_status_write_failed",
                        {
                            "action": "queued",
                            "schedule_id": int(schedule_id),
                            "error": str(exc),
                        },
                    )
            queue_size = len(self._announcement_queue)

        log_schedule(
            "announcement_queue_enqueue",
            {
                "source": source,
                "schedule_id": int(schedule_id),
                "is_one_time": bool(is_one_time),
                "due_minute": due_minute,
                "due_utc": to_storage_utc_z(due_utc),
                "enqueue_seq": int(enqueue_seq),
                "queue_size": queue_size,
            },
        )
        return True

    def _drop_stale_announcement_queue_items(self) -> None:
        """Drop too-old queue items to avoid infinite backlog growth."""
        if not self._announcement_queue:
            return

        now_ts = time.time()
        survivors = deque()
        dropped = 0
        while self._announcement_queue:
            item = self._announcement_queue.popleft()
            age_seconds = max(0, int(now_ts - float(item.get("due_ts", now_ts))))
            if age_seconds <= self._announcement_max_delay_seconds:
                survivors.append(item)
                continue

            dropped += 1
            schedule_id = int(item.get("schedule_id") or 0)
            is_one_time = bool(item.get("is_one_time"))
            self._cleanup_queue_item_tracking(
                item, remove_dedupe=True, cancel_one_time=is_one_time
            )
            self._increment_queue_counter("dropped_stale")
            log_schedule(
                "announcement_queue_dropped_stale",
                {
                    "schedule_id": schedule_id,
                    "is_one_time": is_one_time,
                    "source": item.get("source", "unknown"),
                    "age_seconds": age_seconds,
                },
            )

        self._announcement_queue = survivors
        if dropped:
            logger.warning(
                "Dropped %s stale announcement queue item(s); queue_size=%s",
                dropped,
                len(self._announcement_queue),
            )

    def _mark_announcement_complete_if_done(self) -> None:
        """Mark currently dispatched queue item as finished when session advances."""
        current = self._announcement_current
        if not current:
            return

        player = get_player()
        current_session = current.get("playback_session")
        live_session = getattr(player, "_playback_session", None)

        finished = False
        if current_session is None:
            finished = not player.is_playing
        else:
            finished = (live_session != current_session) or (not player.is_playing)

        if not finished:
            return

        schedule_id = int(current.get("schedule_id") or 0)
        is_one_time = bool(current.get("is_one_time"))
        self._cleanup_queue_item_tracking(
            current, remove_dedupe=False, cancel_one_time=False
        )

        self._announcement_current = None
        self._announcement_next_allowed_monotonic = (
            time.monotonic() + float(self._announcement_gap_seconds)
        )
        self._announcement_last_block_reason = None
        log_schedule(
            "announcement_queue_finish",
            {
                "schedule_id": schedule_id,
                "is_one_time": is_one_time,
                "source": current.get("source", "unknown"),
                "queue_size": len(self._announcement_queue),
            },
        )

    def _is_policy_blocking_announcements(
        self, *, outside_working_hours: bool, silence_blocked: bool
    ) -> tuple[bool, Optional[str]]:
        if silence_blocked:
            return True, "silence_policy"
        if outside_working_hours:
            return True, "outside_working_hours"
        return False, None

    def _log_policy_block_change(self, reason: Optional[str]) -> None:
        """Log policy block only when the reason changes (dedup)."""
        if reason != self._announcement_last_block_reason:
            log_schedule(
                "announcement_queue_blocked_by_policy",
                {
                    "reason": reason,
                    "queue_size": len(self._announcement_queue),
                },
            )
            self._announcement_last_block_reason = reason

    def _handle_failed_dispatch(
        self, item: dict[str, Any], schedule_id: int, is_one_time: bool
    ) -> None:
        """Handle _play_media returning False during queue dispatch.

        Race guard: if policy turned active between the pre-check and the
        actual play call, keep the item queued for the next tick.  Otherwise
        drop it to avoid a permanent jam.
        """
        latest_config = self._get_cached_config()
        latest_decision = resolve_silence_policy(
            latest_config,
            allow_network=False,
            fail_safe_on_unknown=True,
        )
        blocked_now, reason_now = self._is_policy_blocking_announcements(
            outside_working_hours=not is_within_working_hours(latest_config),
            silence_blocked=bool(latest_decision.get("silence_active", False)),
        )
        if blocked_now:
            self._log_policy_block_change(reason_now)
            return

        # Dispatch failed while policy appears clear -> drop this item to avoid jam.
        item = self._announcement_queue.popleft()
        self._cleanup_queue_item_tracking(
            item, remove_dedupe=True, cancel_one_time=is_one_time
        )
        self._announcement_next_allowed_monotonic = (
            time.monotonic() + float(self._announcement_gap_seconds)
        )
        log_schedule(
            "announcement_queue_finish",
            {
                "schedule_id": schedule_id,
                "is_one_time": is_one_time,
                "source": item.get("source", "unknown"),
                "result": "failed_start",
                "queue_size": len(self._announcement_queue),
            },
        )

    def _handle_successful_dispatch(self, item: dict[str, Any]) -> None:
        """Dequeue item, record playback session, and mark as current."""
        item = self._announcement_queue.popleft()
        dedupe_key = item.get("dedupe_key")
        if dedupe_key:
            self._announcement_enqueued_keys.discard(str(dedupe_key))
        item["playback_session"] = getattr(get_player(), "_playback_session", None)
        item["started_ts"] = time.time()
        self._announcement_current = item

    def _process_announcement_queue(
        self,
        *,
        outside_working_hours: bool,
        silence_blocked: bool,
    ) -> None:
        """Queue-Lite dispatcher: ordered, deduped and policy-aware."""
        # Phase 1 (locked): housekeeping + gate checks + extract dispatch info.
        with self._queue_lock:
            self._mark_announcement_complete_if_done()
            self._reset_stuck_current_announcement_if_needed()
            self._drop_stale_announcement_queue_items()
            self._drop_invalid_front_queue_items()

            if self._announcement_current is not None:
                return
            if not self._announcement_queue:
                self._announcement_last_block_reason = None
                return
            if time.monotonic() < self._announcement_next_allowed_monotonic:
                return

            blocked, reason = self._is_policy_blocking_announcements(
                outside_working_hours=outside_working_hours,
                silence_blocked=silence_blocked,
            )
            if blocked:
                self._log_policy_block_change(reason)
                return

            self._announcement_last_block_reason = None
            item = self._announcement_queue[0]
            schedule_id = int(item.get("schedule_id") or 0)
            is_one_time = bool(item.get("is_one_time"))
            filepath = str(item.get("filepath") or "")
            queue_size = len(self._announcement_queue)

        # Phase 2 (unlocked): play media — may block briefly, must not hold lock.
        log_schedule(
            "announcement_queue_start",
            {
                "schedule_id": schedule_id,
                "is_one_time": is_one_time,
                "source": item.get("source", "unknown"),
                "due_utc": item.get("due_utc"),
                "enqueue_seq": int(item.get("enqueue_seq") or 0),
                "queue_size": queue_size,
            },
        )
        success = self._play_media(
            filepath,
            schedule_id=schedule_id,
            is_one_time=is_one_time,
            is_announcement=True,
        )

        # Phase 3 (locked): update state based on dispatch result.
        with self._queue_lock:
            if not success:
                self._handle_failed_dispatch(item, schedule_id, is_one_time)
                return

            self._handle_successful_dispatch(item)

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
            # ── Bug #6 overlap diagnostic ──────────────────────────────────────────
            # Outside working hours: prayer handling is skipped here.
            # If a prayer_pause_state is present, mesai ended WHILE prayer was active
            # (akşam ezanı + mesai bitiş overlap — e.g. 16:55 ezan, 17:00 mesai bitiş).
            # We return False so _handle_working_hours() runs THIS tick and transfers
            # the prayer state via _move_prayer_state_to_working_hours_if_needed().
            # This log fires once per overlap occurrence (state is cleared by transfer).
            # ──────────────────────────────────────────────────────────────────────────
            # DIAGNOSING CUSTOMER COMPLAINTS:
            #   "Music stopped at prayer time and never came back."
            #   → Search logs for 'prayer_active_outside_working_hours' to confirm overlap.
            #   → Then look for 'prayer_state_moved_to_working_hours' (state transferred OK)
            #     OR 'prayer_state_lost_working_hours_already_set' (state discarded — BUG).
            #   → If 'moved' log present: music should resume next mesai start. OK.
            #   → If 'lost' log present: root cause confirmed. Fix: prayer_working_hours_overlap_v2.
            prayer_state = self._get_pause_state("prayer")
            if prayer_state is not None:
                log_prayer(
                    "prayer_active_outside_working_hours",
                    {
                        "index": prayer_state.get("index"),
                        "tracks": len(prayer_state.get("playlist") or []),
                        "note": (
                            "Mesai ended while prayer was active. "
                            "_handle_working_hours will transfer prayer state to "
                            "working_hours slot this tick so music resumes at mesai start."
                        ),
                    },
                )
                logger.info(
                    "Prayer↔WorkingHours overlap: prayer state present but outside "
                    "working hours (index=%s, tracks=%s). "
                    "_handle_working_hours will transfer state this tick. "
                    "If music does not resume at next mesai start, search logs for "
                    "'prayer_state_moved_to_working_hours' or "
                    "'prayer_state_lost_working_hours_already_set'. "
                    "[Ref: prayer_working_hours_overlap_v2]",
                    prayer_state.get("index"),
                    len(prayer_state.get("playlist") or []),
                )
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
            # ── Bug #6 overlap: transfer prayer state if mesai ended during prayer ──
            # When prayer is active and mesai ends simultaneously, _handle_prayer_time()
            # returns False early (not in working hours), so this block runs first.
            # _move_prayer_state_to_working_hours_if_needed() checks if prayer_pause_state
            # is set and transfers it to the working_hours slot — ensuring music resumes
            # at mesai start the NEXT DAY, not incorrectly at prayer end.
            # If prayer_pause_state is None, this is a no-op (normal mesai-end flow).
            # See _move_prayer_state_to_working_hours_if_needed() docstring for full
            # overlap scenario, edge cases, and diagnostic log keys.
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
                self._tick_media_dispatched = False
                config = self._get_cached_config()
                self._refresh_announcement_queue_runtime(config)
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
                outside_working_hours = not is_within_working_hours(config)
                if not prayer_pause_active:
                    outside_working_hours = self._handle_working_hours(config, player)

                # 3. Trigger collection: announcements are queued even if blocked by policy.
                self._check_one_time_schedules(
                    outside_working_hours=outside_working_hours,
                    silence_blocked=prayer_pause_active,
                )
                self._check_recurring_schedules(
                    outside_working_hours=outside_working_hours,
                    silence_blocked=prayer_pause_active,
                )
                self._process_announcement_queue(
                    outside_working_hours=outside_working_hours,
                    silence_blocked=prayer_pause_active,
                )
                self._log_announcement_queue_health()

                self._run_reconcile_watchdog(config, player, silence_decision)

            except Exception as e:
                logger.exception("Scheduler error: %s", e)

            time.sleep(self.check_interval)

    def _check_one_time_schedules(
        self, outside_working_hours: bool, silence_blocked: bool
    ):
        """Check and trigger pending one-time schedules.

        NTP CLOCK SKEW — KNOWN LIMITATION (Backlog: ntp_skew_v2)
        ──────────────────────────────────────────────────────────
        time_diff = (now - scheduled_dt) in seconds. Three zones:

          [0, 120]      fire window  — schedule triggers
          (120, 600]    grace window — slightly missed, retried next tick
          > 600         cancel       — assumed unrecoverable, marked cancelled

        WHY THIS IS A PROBLEM ON RASPBERRY PI / EMBEDDED DEVICES:
        At boot without internet the system clock may be wrong (RTC drift or
        no RTC battery). When NTP syncs and the clock jumps FORWARD by minutes
        or hours, any schedule whose scheduled_dt has already passed by > 600 s
        is silently cancelled — it never plays.

        MITIGATION (this release): cancel threshold raised from 300 s → 600 s
        (10 min buffer instead of 5 min) to cover typical slow-boot NTP sync.

        FULL FIX: ntp_skew_v2 — boot-time backfill pass + monotonic trigger
        tracking so schedules that were missed during offline boot are replayed.

        HOW TO DIAGNOSE "schedule never played":
          1. Search 'one_time_schedule_missed_cancelled' → cancelled? Check
             'time_diff_seconds': if much > 600, NTP forward jump is the cause.
          2. Search 'one_time_schedule_grace_window' → sat in grace, then cancelled?
          3. Search 'one_time_schedule_clock_skew_negative' → NTP backward sync?
          4. If none of the above: check 'one_time_status_write_failed' for DB errors.
          Fix: deploy ntp_skew_v2 release.
        """
        now = now_utc()
        pending = db.get_pending_one_time_schedules()

        for schedule in pending:
            scheduled_dt = parse_storage_datetime_to_utc(
                schedule.get("scheduled_datetime"), naive_as_local=True
            )
            if scheduled_dt is None:
                # Invalid datetime format - mark as cancelled to prevent infinite retry
                logger.error(
                    f"Invalid datetime for schedule #{schedule['id']}: '{schedule['scheduled_datetime']}' - marking as cancelled"
                )
                try:
                    db.update_one_time_schedule_status(schedule["id"], "cancelled")
                except Exception as exc:
                    logger.exception(
                        "Failed to cancel invalid one-time schedule id=%s",
                        schedule.get("id"),
                    )
                    log_error(
                        "one_time_status_write_failed",
                        {
                            "action": "cancelled_invalid_datetime",
                            "schedule_id": schedule.get("id"),
                            "error": str(exc),
                        },
                    )
                continue

            # Check if it's time (within 2 minute tolerance for safety)
            time_diff = (now - scheduled_dt).total_seconds()
            schedule_id = int(schedule["id"])
            media_type = self._normalize_media_type(schedule.get("media_type"))
            is_announcement = media_type == "announcement"
            filename = schedule.get("filename", "unknown")

            if 0 <= time_diff <= 120:
                if is_announcement:
                    if outside_working_hours:
                        logger.warning(
                            f"One-time announcement outside working hours, skipping: "
                            f"{filename} (scheduled {scheduled_dt})"
                        )
                        try:
                            db.update_one_time_schedule_status(schedule_id, "cancelled")
                        except Exception as exc:
                            logger.exception(
                                "Failed to cancel one-time announcement outside working hours id=%s",
                                schedule_id,
                            )
                            log_error(
                                "one_time_status_write_failed",
                                {
                                    "action": "cancelled_outside_working_hours",
                                    "schedule_id": schedule_id,
                                    "error": str(exc),
                                },
                            )
                        continue
                    duration_seconds = resolve_duration_seconds(schedule.get("media_id"))
                    self._queue_announcement(
                        filepath=schedule["filepath"],
                        schedule_id=schedule_id,
                        is_one_time=True,
                        due_dt=scheduled_dt,
                        source="one_time",
                        duration_seconds=duration_seconds,
                    )
                    continue

                if outside_working_hours:
                    logger.warning(
                        f"Schedule outside working hours, cancelling: {filename} (was scheduled for {scheduled_dt})"
                    )
                    try:
                        db.update_one_time_schedule_status(schedule_id, "cancelled")
                    except Exception as exc:
                        logger.exception(
                            "Failed to cancel one-time schedule outside working hours id=%s",
                            schedule_id,
                        )
                        log_error(
                            "one_time_status_write_failed",
                            {
                                "action": "cancelled_outside_working_hours",
                                "schedule_id": schedule_id,
                                "error": str(exc),
                            },
                        )
                elif silence_blocked:
                    # Keep behavior for non-announcement content: do not queue under silence.
                    continue
                else:
                    # Time to play!
                    logger.info(
                        f"Triggering one-time schedule: {filename} (diff: {time_diff:.0f}s)"
                    )
                    log_trigger(
                        "one_time",
                        {
                            "filename": filename,
                            "media_type": media_type,
                            "delay_seconds": int(time_diff),
                        },
                    )
                    self._play_media(
                        schedule["filepath"],
                        schedule_id,
                        is_one_time=True,
                        is_announcement=False,
                    )

            elif time_diff < 0:
                # ── NTP BACKWARD SYNC DIAGNOSTIC ────────────────────────────────────
                # schedule_dt is in the FUTURE relative to now. This happens when:
                #   a) NTP syncs and jumps the clock backward (corrects a fast clock)
                #   b) The schedule was created with a future time (normal)
                # Case (a) is self-healing: when the clock catches up, the schedule
                # will enter the [0, 120] fire window and trigger normally.
                # Case (b) is also normal; no action needed.
                #
                # WHEN TO WORRY: if the same schedule_id appears here for many ticks
                # AND never enters [0, 120], the clock may be permanently wrong.
                # Search logs for this schedule_id to see if it eventually fired.
                # ────────────────────────────────────────────────────────────────────
                if time_diff < -30:
                    # Only log when significantly in the future (>30 s) to avoid
                    # noisy logs from sub-second clock precision differences.
                    log_schedule(
                        "one_time_schedule_clock_skew_negative",
                        {
                            "schedule_id": schedule_id,
                            "filename": filename,
                            "scheduled_at": str(scheduled_dt),
                            "now_utc": str(now),
                            "time_diff_seconds": round(time_diff, 1),
                            "note": (
                                "Schedule is in the future. Possible NTP backward sync "
                                "or normal future schedule. Will retry when clock catches up."
                            ),
                        },
                    )
                    logger.warning(
                        "One-time schedule #%s ('%s') is %.0fs in the future "
                        "(scheduled: %s, now: %s). "
                        "Possible NTP backward clock sync — will retry on next tick. "
                        "[Ref: ntp_skew_v2]",
                        schedule_id,
                        filename,
                        abs(time_diff),
                        scheduled_dt,
                        now,
                    )

            elif 120 < time_diff <= 600:
                # ── GRACE WINDOW — schedule slightly missed ──────────────────────────
                # The trigger window [0, 120] was missed (scheduler was briefly offline,
                # system was busy, or NTP jumped forward by a small amount).
                # We do NOT cancel yet — the schedule stays pending and will be
                # retried on every tick until time_diff exceeds 600 s.
                #
                # WHEN TO WORRY: if a schedule sits here for many ticks and then
                # gets cancelled (search 'one_time_schedule_missed_cancelled'), the
                # most likely cause is a slow-boot NTP sync that jumped the clock
                # forward past the trigger window before the scheduler first ran.
                # Fix: ntp_skew_v2 (boot backfill).
                # ────────────────────────────────────────────────────────────────────
                log_schedule(
                    "one_time_schedule_grace_window",
                    {
                        "schedule_id": schedule_id,
                        "filename": filename,
                        "scheduled_at": str(scheduled_dt),
                        "time_diff_seconds": round(time_diff, 1),
                        "cancel_threshold_seconds": 600,
                        "note": (
                            "Schedule missed trigger window [0, 120] but is within "
                            "grace window (120, 600]. Will retry next tick. "
                            "If cancelled shortly after, NTP forward sync at boot "
                            "is the likely cause."
                        ),
                    },
                )
                logger.info(
                    "One-time schedule #%s ('%s') in grace window (%.0fs past due, "
                    "cancel threshold 600s). Retrying next tick. "
                    "If cancelled soon after, check for NTP boot sync. [Ref: ntp_skew_v2]",
                    schedule_id,
                    filename,
                    time_diff,
                )

            elif time_diff > 600:
                # ── CANCEL THRESHOLD ────────────────────────────────────────────────
                # Threshold raised from 300 s → 600 s (mitigation for NTP boot sync).
                # Gives a 10-minute buffer for slow Pi boot + NTP convergence.
                #
                # DIAGNOSING MISSED SCHEDULES:
                # If customer reports "the morning announcement never played", search:
                #   'one_time_schedule_missed_cancelled' — was it cancelled here?
                #   Check 'time_diff_seconds': >> 600 → NTP jump at boot is the cause.
                #   Check 'one_time_schedule_grace_window' before this log — did it
                #   sit in grace or jump straight here (large NTP forward sync)?
                # Full fix: ntp_skew_v2 release.
                # ────────────────────────────────────────────────────────────────────
                if schedule_id in self._queued_one_time_ids:
                    # Already queued for playback — do not cancel mid-flight.
                    continue

                log_error(
                    "one_time_schedule_missed_cancelled",
                    {
                        "schedule_id": schedule_id,
                        "filename": filename,
                        "scheduled_at": str(scheduled_dt),
                        "now_utc": str(now),
                        "time_diff_seconds": round(time_diff, 1),
                        "cancel_threshold_seconds": 600,
                        "impact": (
                            "Schedule was not played. If time_diff >> 600, "
                            "NTP forward sync at boot is the likely cause. "
                            "Deploy ntp_skew_v2 for permanent fix."
                        ),
                        "backlog_ref": "ntp_skew_v2",
                    },
                )
                logger.error(
                    "One-time schedule #%s ('%s') missed — cancelling. "
                    "Was due at %s, now %s (%.0fs overdue, threshold 600s). "
                    "If time_diff is much > 600, NTP forward clock sync at boot "
                    "is likely the cause. Search 'one_time_schedule_grace_window' "
                    "for this schedule_id to see full history. [Ref: ntp_skew_v2]",
                    schedule_id,
                    filename,
                    scheduled_dt,
                    now,
                    time_diff,
                )
                try:
                    db.update_one_time_schedule_status(schedule_id, "cancelled")
                except Exception as exc:
                    logger.exception(
                        "Failed to cancel missed one-time schedule id=%s",
                        schedule_id,
                    )
                    log_error(
                        "one_time_status_write_failed",
                        {
                            "action": "cancelled_missed",
                            "schedule_id": schedule_id,
                            "error": str(exc),
                        },
                    )

    def _check_recurring_schedules(
        self, outside_working_hours: bool, silence_blocked: bool
    ):
        """Check and trigger active recurring schedules."""
        now = now_local()
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

                media_type = self._normalize_media_type(schedule.get("media_type"))
                is_announcement = media_type == "announcement"
                if is_announcement:
                    if outside_working_hours:
                        continue
                    duration_seconds = resolve_duration_seconds(schedule.get("media_id"))
                    self._queue_announcement(
                        filepath=schedule["filepath"],
                        schedule_id=schedule_id,
                        is_one_time=False,
                        due_dt=now.replace(second=0, microsecond=0),
                        source="recurring",
                        duration_seconds=duration_seconds,
                    )
                else:
                    if outside_working_hours or silence_blocked:
                        continue
                    logger.info(f"Triggering recurring schedule: {schedule['filename']}")
                    log_trigger(
                        "recurring",
                        {
                            "filename": schedule["filename"],
                            "media_type": media_type,
                            "schedule_id": schedule_id,
                        },
                    )
                    self._play_media(
                        schedule["filepath"],
                        schedule_id,
                        is_one_time=False,
                        is_announcement=False,
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

    def _queue_restore_target(self, snapshot: dict[str, Any], volume_override_active: bool = False) -> bool:
        if not snapshot["playlist_was_active"] or not snapshot["playlist_files"]:
            return False

        restore_state = {
            "playlist": list(snapshot["playlist_files"]),
            "index": snapshot["playlist_index"],
            "loop": snapshot["playlist_loop"],
            "active": True,
            "volume_override_active": volume_override_active,
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

        player.apply_playlist_state(
            playlist=playlist,
            index=max(index - 1, -1),
            loop=loop,
            runtime_active=True,
            db_active=True,
            play_next=True,
        )
        log_schedule("restore_worker_playlist_resumed", {
            "thread_id": threading.current_thread().name,
            "index": index,
            "total_tracks": len(playlist),
            "loop": loop,
            "volume_override_active": bool(state.get("volume_override_active", False)),
        })
        return True

    _RESTORE_PLAYER_WAIT_TIMEOUT_S: int = 120

    def _run_restore_worker(self, player, done_event: Optional[threading.Event] = None) -> None:
        """Restore playlist after announcement playback.

        done_event is captured at _play_media() call-site and passed here so this worker
        always signals the correct Event even when a second announcement overwrites
        self._announcement_done before this thread reaches STEP 2.

        Bug guard: do NOT read self._announcement_done here — it may already point to a
        newer Event belonging to a concurrent announcement. Use the passed done_event only.
        """
        try:
            while True:
                _wait_deadline = time.monotonic() + self._RESTORE_PLAYER_WAIT_TIMEOUT_S
                while player.is_playing:
                    if time.monotonic() >= _wait_deadline:
                        logger.error(
                            "Restore worker: player.is_playing did not clear within %ds "
                            "— mpg123 may be hung. Aborting restore.",
                            self._RESTORE_PLAYER_WAIT_TIMEOUT_S,
                        )
                        return
                    time.sleep(0.5)

                # STEP 1: Restore volume BEFORE playlist resume
                with self._restore_lock:
                    state = self._restore_target_state  # peek, don't consume

                if state and state.get("volume_override_active", False):
                    token = _volume_runtime.get_override_token()
                    restored = _volume_runtime.restore_override(
                        reason="announcement_ended_sequential",
                        token=token,
                    )
                    if not restored and token is not None:
                        time.sleep(0.1)
                        token = _volume_runtime.get_override_token()
                        if token is not None:
                            _volume_runtime.restore_override(
                                reason="announcement_ended_sequential_retry",
                                token=token,
                            )
                    log_schedule("restore_worker_volume_restored", {
                        "thread_id": threading.current_thread().name,
                        "token": token,
                        "restored": restored,
                        "volume_override_active": True,
                    })

                # STEP 2: Signal announcement done event.
                # Use done_event (captured at setup) — NOT self._announcement_done.
                # self._announcement_done may already point to a second announcement's
                # Event if back-to-back announcements are dispatched. Signalling the wrong
                # Event would leave resume_worker_A waiting 120 s on Event_A which is
                # never set. See: _play_media() where done_event is created and passed.
                if done_event is not None and not done_event.is_set():
                    done_event.set()
                self._announcement_done = None  # clear shared ref regardless

                # STEP 3: Resume playlist (volume is already canonical)
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

    def _start_restore_worker(self, player, done_event: Optional[threading.Event] = None) -> None:
        restore_thread = threading.Thread(
            target=self._run_restore_worker, args=(player, done_event), daemon=True
        )
        with self._restore_lock:
            self._restore_threads.append(restore_thread)
        try:
            restore_thread.start()
        except Exception:
            with self._restore_lock:
                self._restore_in_progress = False
                if restore_thread in self._restore_threads:
                    self._restore_threads.remove(restore_thread)
            raise

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
    ) -> bool:
        """Trigger media playback with announcement priority.

        Only one successful dispatch is allowed per scheduler tick to prevent
        music/announcement collisions that corrupt restore snapshots.
        """
        if self._tick_media_dispatched:
            log_schedule(
                "scheduled_media_skipped_tick_guard",
                {
                    "schedule_id": schedule_id,
                    "is_one_time": bool(is_one_time),
                    "is_announcement": bool(is_announcement),
                    "filepath": filepath,
                },
            )
            return False

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
            log_schedule(
                "scheduled_media_blocked_policy",
                {
                    "schedule_id": schedule_id,
                    "is_one_time": bool(is_one_time),
                    "is_announcement": bool(is_announcement),
                    "policy": policy_decision.get("policy"),
                    "reason_code": policy_decision.get("reason_code"),
                },
            )
            logger.info(
                "Skipping scheduled media due to silence policy "
                f"(schedule_id={schedule_id}, policy={policy_decision.get('policy')}, "
                f"reason={policy_decision.get('reason_code')})"
            )
            if is_one_time and not is_announcement:
                db.update_one_time_schedule_status(schedule_id, "cancelled")
            return False

        if not is_within_working_hours(config):
            if is_one_time and not is_announcement:
                db.update_one_time_schedule_status(schedule_id, "cancelled")
            return False

        stream_status = stream_service.status()
        stream_active = bool(stream_status.get("active"))

        if not is_announcement and should_skip_scheduled_music(stream_active):
            logger.info(
                "Skipping scheduled music while stream is active "
                f"(schedule_id={schedule_id})"
            )
            if is_one_time:
                db.update_one_time_schedule_status(schedule_id, "cancelled")
            return False

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
                log_error(
                    "scheduled_override_volume_apply_failed",
                    {
                        "schedule_id": schedule_id,
                        "override_volume": override_volume,
                        "canonical_volume": int(canonical_volume.get("volume", 0)),
                        "source_type": source_type,
                    },
                )
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
                start_watcher=False,
            )
        elif not success and override_applied:
            player.set_volume(int(canonical_volume.get("volume", 0)))
        # Create done_event locally BEFORE spawning any workers so every worker that
        # needs to signal or wait on it holds the same object reference. Reading
        # self._announcement_done inside a worker thread is unsafe: a concurrent
        # announcement can overwrite self._announcement_done before the worker reaches
        # its signal step, causing the resume worker to wait 120 s on an orphaned Event.
        done_event: Optional[threading.Event] = None
        if announcement_interrupted_stream:
            done_event = threading.Event()
            self._announcement_done = done_event
        restore_queued = success and self._queue_restore_target(
            snapshot, volume_override_active=override_applied
        )
        if restore_queued:
            self._start_restore_worker(player, done_event=done_event)
        elif success and override_applied:
            # No playlist to restore but volume override is active — fallback to session watcher
            _volume_runtime._start_session_watcher(_volume_runtime._override_token)
        elif announcement_interrupted_stream:
            # No restore worker to signal the event — start a lightweight sentinel.
            # Capture done_event (not self._announcement_done) so the closure holds the
            # correct Event even if self._announcement_done is overwritten by a later call.
            _evt = done_event
            def _signal_on_player_stop():
                _p = get_player()
                deadline = time.monotonic() + 120.0
                while _p.is_playing and time.monotonic() < deadline:
                    time.sleep(0.2)
                if _evt is not None:
                    _evt.set()
            threading.Thread(target=_signal_on_player_stop, daemon=True).start()
        if announcement_interrupted_stream:
            self._start_stream_resume_worker_after_announcement()
        self._finalize_one_time_status(success, schedule_id, is_one_time)
        if success:
            self._tick_media_dispatched = True
        return bool(success)

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
