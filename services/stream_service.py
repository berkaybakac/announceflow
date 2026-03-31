"""
AnnounceFlow - Stream Service
Stream session orchestration: start/stop/status.

Responsibilities:
- Manage single active stream session
- Coordinate with player (stop playlist on stream start, restore on stop)
- Expose StreamStatus contract
- Delegate receiver lifecycle to StreamManager
- Delegate policy decisions to StreamPolicy

V1 scope: single session, same LAN, no DB persistence for stream state.
"""
import logging
import math
import os
import threading
import time
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from logger import log_error, log_system

logger = logging.getLogger(__name__)

# Seconds after the *last heartbeat* before a stream is auto-stopped.
# Monitoring only activates once the first heartbeat() call is received
# (i.e. _last_heartbeat_at > 0), so senders that never call heartbeat
# are never timed out — backward-compatible with old clients.
HEARTBEAT_TIMEOUT = 15.0
COMMAND_TTL_SECONDS = 45.0
AGENT_ONLINE_TTL_SECONDS = 20.0

# Xrun auto-restart thresholds.
# If xrun count increases by this many within the rolling window, restart receiver.
XRUN_RESTART_THRESHOLD = 100
# Rolling window duration in seconds (5 minutes).
XRUN_RESTART_WINDOW_SECONDS = 300.0
# By default, only emit alarms/telemetry when threshold is exceeded.
XRUN_AUTO_RECOVERY_DRY_RUN_DEFAULT = True
# Max auto-restarts per hour to prevent restart loops.
XRUN_MAX_RESTARTS_PER_HOUR = 3
# Cooldown after a successful auto-restart to avoid burst restart loops.
XRUN_AUTO_RESTART_COOLDOWN_SECONDS = 60.0

_XRUN_DRY_RUN_ENV = "ANNOUNCEFLOW_XRUN_AUTO_RECOVERY_DRY_RUN"
_XRUN_THRESHOLD_ENV = "ANNOUNCEFLOW_XRUN_RESTART_THRESHOLD"
_XRUN_WINDOW_ENV = "ANNOUNCEFLOW_XRUN_RESTART_WINDOW_SECONDS"
_xrun_policy_cache_lock = threading.Lock()
_xrun_policy_cache: Dict[str, Any] = {
    "threshold": XRUN_RESTART_THRESHOLD,
    "window_seconds": XRUN_RESTART_WINDOW_SECONDS,
    "dry_run": XRUN_AUTO_RECOVERY_DRY_RUN_DEFAULT,
    "raw_values": (None, None, None),
}


def _coerce_non_negative_int(value: Any, default: int = 0) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return max(0, parsed)


def _coerce_non_negative_float(value: Any, default: float = 0.0) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(parsed):
        return default
    return max(0.0, parsed)


def _read_env_int(
    name: str,
    default: int,
    *,
    min_value: int = 1,
    raw: Optional[str] = None,
) -> int:
    raw_value = os.environ.get(name) if raw is None else raw
    if raw_value is None:
        return default
    try:
        value = int(str(raw_value).strip())
    except (TypeError, ValueError):
        return default
    if value < min_value:
        return default
    return value


def _read_env_float(
    name: str,
    default: float,
    *,
    min_value: float = 1.0,
    raw: Optional[str] = None,
) -> float:
    raw_value = os.environ.get(name) if raw is None else raw
    if raw_value is None:
        return default
    try:
        value = float(str(raw_value).strip())
    except (TypeError, ValueError):
        return default
    if value < min_value:
        return default
    return value


def _read_env_bool(name: str, default: bool, *, raw: Optional[str] = None) -> bool:
    raw_value = os.environ.get(name) if raw is None else raw
    if raw_value is None:
        return default
    text = str(raw_value).strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    return default


def _get_xrun_policy_config() -> tuple[int, float, bool]:
    raw_threshold = os.environ.get(_XRUN_THRESHOLD_ENV)
    raw_window = os.environ.get(_XRUN_WINDOW_ENV)
    raw_dry_run = os.environ.get(_XRUN_DRY_RUN_ENV)
    raw_values = (raw_threshold, raw_window, raw_dry_run)
    with _xrun_policy_cache_lock:
        cached_raw_values = _xrun_policy_cache.get("raw_values", (None, None, None))
        if raw_values == cached_raw_values:
            return (
                int(_xrun_policy_cache["threshold"]),
                float(_xrun_policy_cache["window_seconds"]),
                bool(_xrun_policy_cache["dry_run"]),
            )

        threshold = _read_env_int(
            _XRUN_THRESHOLD_ENV,
            XRUN_RESTART_THRESHOLD,
            min_value=1,
            raw=raw_threshold,
        )
        window_seconds = _read_env_float(
            _XRUN_WINDOW_ENV,
            XRUN_RESTART_WINDOW_SECONDS,
            min_value=1.0,
            raw=raw_window,
        )
        dry_run = _read_env_bool(
            _XRUN_DRY_RUN_ENV,
            XRUN_AUTO_RECOVERY_DRY_RUN_DEFAULT,
            raw=raw_dry_run,
        )
        _xrun_policy_cache["threshold"] = threshold
        _xrun_policy_cache["window_seconds"] = window_seconds
        _xrun_policy_cache["dry_run"] = dry_run
        _xrun_policy_cache["raw_values"] = raw_values
        return threshold, window_seconds, dry_run


def _new_correlation_id() -> str:
    return f"stream-{int(time.time() * 1000)}-{threading.get_ident()}"


def _utc_iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat(
        timespec="seconds"
    ).replace("+00:00", "Z")


class StreamStatus:
    """Stream status data contract (V1).

    Fields (immutable per PI4_STREAM_V1_SCOPE.md section 5.3):
        active: bool
        state: idle | live | paused_for_announcement | stopped_by_policy | error
        source_before_stream: playlist | none
        last_error: str | None
    """

    def __init__(
        self,
        active: bool = False,
        state: str = "idle",
        source_before_stream: str = "none",
        last_error=None,
    ):
        self.active = active
        self.state = state
        self.source_before_stream = source_before_stream
        self.last_error = last_error

    def to_dict(self) -> dict:
        return {
            "active": self.active,
            "state": self.state,
            "source_before_stream": self.source_before_stream,
            "last_error": self.last_error,
        }


class StreamService:
    """Orchestrates stream sessions."""

    def __init__(self, stream_manager=None, player_fn=None):
        self._manager = stream_manager
        if player_fn is None:
            from player import get_player

            player_fn = get_player
        self._player_fn = player_fn
        self._status = StreamStatus()
        self._lock = threading.Lock()
        self._policy_resume_armed = False
        self._user_stopped = False
        self._active_correlation_id: Optional[str] = None
        self._active_device_id: Optional[str] = None
        self._active_device_name: Optional[str] = None
        self._preferred_device_id: Optional[str] = None
        self._preferred_device_name: Optional[str] = None
        # Monotonic timestamp of session start (for duration calculation).
        self._session_started_at: float = 0.0
        # Monotonic timestamp of the last accepted heartbeat.
        # Stays 0.0 until the first heartbeat() call is received so that
        # old clients that never call heartbeat are never auto-stopped.
        self._last_heartbeat_at: float = 0.0
        # Set to True while a takeover is between its two lock sections
        # (old receiver stopped, new one not yet started).  Prevents a
        # concurrent start() from racing into an inconsistent state.
        self._mid_takeover: bool = False
        # Desired-state command plane (panel -> agent).
        self._desired_stream_on: bool = False
        self._desired_generation: int = 0
        self._desired_target_device_id: Optional[str] = None
        self._desired_updated_at: float = 0.0
        self._active_command_id: Optional[str] = None
        self._command_status: str = "idle"  # idle|pending|applied|failed|expired
        self._command_error: Optional[str] = None
        self._agent_registry: Dict[str, Dict[str, Any]] = {}
        # Xrun auto-restart: rolling window tracking
        self._xrun_last_known_count: int = 0
        self._xrun_window_start_mono: float = 0.0
        self._xrun_window_start_count: int = 0
        self._xrun_auto_restart_times: list[float] = []
        self._xrun_restart_intent_seq: int = 0
        self._xrun_restart_intent_id: Optional[str] = None
        self._xrun_restart_cooldown_until_mono: float = 0.0
        self._start_heartbeat_monitor()

    # ------------------------------------------------------------------
    # Heartbeat monitor
    # ------------------------------------------------------------------

    def _start_heartbeat_monitor(self) -> None:
        t = threading.Thread(target=self._heartbeat_monitor_loop, daemon=True)
        t.name = "stream-heartbeat-monitor"
        t.start()

    def _heartbeat_monitor_loop(self) -> None:
        while True:
            time.sleep(5)
            try:
                self._check_heartbeat()
            except Exception as exc:
                logger.error("StreamService: heartbeat monitor error: %s", exc)
            try:
                self._check_xrun_auto_restart()
            except Exception as exc:
                logger.error("StreamService: xrun auto-restart check error: %s", exc)

    def _check_heartbeat(self) -> bool:
        """Check heartbeat expiry; auto-stop if expired.

        Monitoring activates only after the first heartbeat() call sets
        _last_heartbeat_at > 0, regardless of whether a device_id is set.
        This means old senders that never call heartbeat are never timed out.

        Returns True when stream was auto-stopped, False otherwise.
        Designed to be called from the monitor loop or directly in tests.
        """
        should_stop = False
        evicted_device = None
        evicted_cid = None
        with self._lock:
            if (
                self._status.active
                and self._status.state == "live"
                and self._last_heartbeat_at > 0 
            ):
                elapsed = time.monotonic() - self._last_heartbeat_at
                if elapsed >= HEARTBEAT_TIMEOUT:
                    should_stop = True
                    evicted_device = self._active_device_id
                    evicted_cid = self._active_correlation_id
        if should_stop:
            logger.warning(
                "StreamService: heartbeat expired (device=%s, cid=%s), auto-stopping",
                evicted_device,
                evicted_cid,
            )
            log_system(
                "stream_heartbeat_expired",
                {"device_id": evicted_device, "correlation_id": evicted_cid},
            )
            self.stop()
            return True
        return False

    def _check_xrun_auto_restart(self) -> bool:
        """Auto-restart receiver if xrun rate exceeds threshold.

        Reads the xrun status file written by the receiver subprocess.
        If xrun count increased by >= XRUN_RESTART_THRESHOLD within a
        rolling XRUN_RESTART_WINDOW_SECONDS window, stop and restart
        the receiver.  Throttled to XRUN_MAX_RESTARTS_PER_HOUR.

        Returns True if a restart was triggered.
        """
        if not self._manager:
            return False

        status = self._manager.read_xrun_status()
        if status is None:
            return False

        threshold, window_seconds, dry_run = _get_xrun_policy_config()
        current_xrun = _coerce_non_negative_int(status.get("alsa_xrun"), default=0)
        if current_xrun <= 0:
            return False

        status_correlation_id = str(status.get("correlation_id") or "").strip() or None
        status_peak_1s = _coerce_non_negative_int(status.get("xrun_peak_1s"), default=0)
        status_peak_60s = _coerce_non_negative_int(status.get("xrun_peak_60s"), default=0)
        status_max_consecutive = _coerce_non_negative_int(
            status.get("xrun_max_consecutive"),
            default=0,
        )
        status_current_consecutive = _coerce_non_negative_int(
            status.get("xrun_current_consecutive"),
            default=0,
        )
        status_session_rate = _coerce_non_negative_float(
            status.get("xrun_session_rate_per_sec"),
            default=0.0,
        )
        status_burst_rate = _coerce_non_negative_float(
            status.get("xrun_burst_rate_per_sec"),
            default=0.0,
        )
        now = time.monotonic()
        intent_id: Optional[str] = None
        correlation_id: Optional[str] = None
        xruns_in_window = 0
        restarts_this_hour = 0
        event_name: Optional[str] = None
        event_payload: Optional[dict] = None

        def _xrun_event_payload(*, reason: str, restarts_count: int, state: str, active: bool) -> dict:
            return {
                "correlation_id": correlation_id,
                "xruns_in_window": xruns_in_window,
                "total_xruns": current_xrun,
                "restarts_this_hour": restarts_count,
                "state": state,
                "active": active,
                "reason": reason,
                "dry_run": dry_run,
                "threshold": threshold,
                "window_seconds": window_seconds,
                "xrun_peak_1s": status_peak_1s,
                "xrun_peak_60s": status_peak_60s,
                "xrun_max_consecutive": status_max_consecutive,
                "xrun_current_consecutive": status_current_consecutive,
                "xrun_session_rate_per_sec": status_session_rate,
                "xrun_burst_rate_per_sec": status_burst_rate,
            }

        with self._lock:
            if not self._status.active or self._status.state != "live":
                return False
            correlation_id = self._active_correlation_id
            if not correlation_id:
                return False

            # If the correlation_id changed (new session), ignore stale status.
            if status_correlation_id != correlation_id:
                return False

            # Initialize window on first observation.
            if self._xrun_window_start_mono == 0.0:
                self._xrun_window_start_mono = now
                self._xrun_window_start_count = current_xrun
                self._xrun_last_known_count = current_xrun
                return False

            # Slide window if it expired.
            if now - self._xrun_window_start_mono > window_seconds:
                self._xrun_window_start_mono = now
                self._xrun_window_start_count = current_xrun

            xruns_in_window = current_xrun - self._xrun_window_start_count
            self._xrun_last_known_count = current_xrun
            if xruns_in_window < threshold:
                return False

            # Throttle: max N restarts per hour.
            cutoff = now - 3600.0
            self._xrun_auto_restart_times = [
                ts for ts in self._xrun_auto_restart_times if ts > cutoff
            ]
            restarts_this_hour = len(self._xrun_auto_restart_times)
            if restarts_this_hour >= XRUN_MAX_RESTARTS_PER_HOUR:
                event_name = "stream_xrun_auto_restart_skipped_throttled"
                event_payload = _xrun_event_payload(
                    reason="restart_budget_exhausted",
                    restarts_count=restarts_this_hour,
                    state=self._status.state,
                    active=bool(self._status.active),
                )
            elif now < self._xrun_restart_cooldown_until_mono:
                event_name = "stream_xrun_auto_restart_skipped_cooldown"
                event_payload = _xrun_event_payload(
                    reason="cooldown_active",
                    restarts_count=restarts_this_hour,
                    state=self._status.state,
                    active=bool(self._status.active),
                )
            elif dry_run:
                # Re-arm window after each dry-run alarm to avoid repeating the
                # same threshold crossing every monitor tick.
                self._xrun_window_start_mono = now
                self._xrun_window_start_count = current_xrun
                event_name = "stream_xrun_auto_restart_dry_run"
                event_payload = _xrun_event_payload(
                    reason="threshold_exceeded_dry_run",
                    restarts_count=restarts_this_hour,
                    state=self._status.state,
                    active=bool(self._status.active),
                )
            else:
                self._xrun_restart_intent_seq += 1
                intent_id = (
                    f"xrun-intent-{self._xrun_restart_intent_seq}-{int(time.time() * 1000)}"
                )
                self._xrun_restart_intent_id = intent_id

        if event_name and event_payload:
            if event_name == "stream_xrun_auto_restart_skipped_throttled":
                logger.warning(
                    "StreamService: xrun auto-restart skipped, throttle exhausted "
                    "(xruns_in_window=%d, cid=%s)",
                    xruns_in_window,
                    correlation_id,
                )
            elif event_name == "stream_xrun_auto_restart_skipped_cooldown":
                logger.info(
                    "StreamService: xrun auto-restart skipped, cooldown active "
                    "(xruns_in_window=%d, cid=%s)",
                    xruns_in_window,
                    correlation_id,
                )
            elif event_name == "stream_xrun_auto_restart_dry_run":
                logger.warning(
                    "[DRY-RUN ALARM] XRUN threshold exceeded "
                    "(xruns_in_window=%d threshold=%d window_seconds=%.1f cid=%s)",
                    xruns_in_window,
                    threshold,
                    window_seconds,
                    correlation_id,
                )
            log_system(event_name, event_payload)
            return False

        if not intent_id or not correlation_id:
            return False

        logger.warning(
            "StreamService: xrun auto-restart decision "
            "(xruns_in_window=%d threshold=%d cid=%s intent_id=%s)",
            xruns_in_window,
            threshold,
            correlation_id,
            intent_id,
        )

        if not self._manager.stop_receiver():
            with self._lock:
                if self._xrun_restart_intent_id == intent_id:
                    self._xrun_restart_intent_id = None
                event_payload = _xrun_event_payload(
                    reason="stop_receiver_failed",
                    restarts_count=len(self._xrun_auto_restart_times),
                    state=self._status.state,
                    active=bool(self._status.active),
                )
            log_system("stream_xrun_auto_restart_failed", event_payload)
            return False

        self._manager.wait_for_stop_complete()

        with self._lock:
            abort_reason: Optional[str] = None
            if self._xrun_restart_intent_id != intent_id:
                abort_reason = "intent_superseded"
            elif self._user_stopped:
                abort_reason = "user_stopped"
            elif not self._status.active or self._status.state != "live":
                abort_reason = "state_changed_before_restart"
            elif self._active_correlation_id != correlation_id:
                abort_reason = "correlation_changed_before_restart"

            if abort_reason:
                if self._xrun_restart_intent_id == intent_id:
                    self._xrun_restart_intent_id = None
                event_payload = _xrun_event_payload(
                    reason=abort_reason,
                    restarts_count=len(self._xrun_auto_restart_times),
                    state=self._status.state,
                    active=bool(self._status.active),
                )
                logger.info(
                    "StreamService: xrun auto-restart aborted, "
                    "reason=%s cid=%s intent_id=%s",
                    abort_reason,
                    correlation_id,
                    intent_id,
                )
                event_name = "stream_xrun_auto_restart_aborted"
            else:
                start_ok = self._manager.start_receiver(
                    correlation_id=correlation_id,
                    wait_for_stop=True,
                )
                if not start_ok:
                    if self._xrun_restart_intent_id == intent_id:
                        self._xrun_restart_intent_id = None
                    event_name = "stream_xrun_auto_restart_failed"
                    event_payload = _xrun_event_payload(
                        reason="start_receiver_failed",
                        restarts_count=len(self._xrun_auto_restart_times),
                        state=self._status.state,
                        active=bool(self._status.active),
                    )
                else:
                    restart_at = time.monotonic()
                    self._xrun_auto_restart_times.append(restart_at)
                    self._reset_xrun_tracking()
                    self._xrun_restart_cooldown_until_mono = (
                        restart_at + XRUN_AUTO_RESTART_COOLDOWN_SECONDS
                    )
                    event_name = "stream_xrun_auto_restart"
                    event_payload = _xrun_event_payload(
                        reason="threshold_exceeded",
                        restarts_count=len(self._xrun_auto_restart_times),
                        state=self._status.state,
                        active=bool(self._status.active),
                    )
                    logger.warning(
                        "StreamService: xrun auto-restart executed "
                        "(xruns_in_window=%d threshold=%d cid=%s intent_id=%s)",
                        xruns_in_window,
                        threshold,
                        correlation_id,
                        intent_id,
                    )

        if event_name and event_payload:
            log_system(event_name, event_payload)
        return event_name == "stream_xrun_auto_restart"

    def _reset_xrun_tracking(self) -> None:
        """Reset xrun auto-restart state (call on session stop/start)."""
        self._xrun_last_known_count = 0
        self._xrun_window_start_mono = 0.0
        self._xrun_window_start_count = 0
        self._xrun_restart_intent_id = None
        self._xrun_restart_cooldown_until_mono = 0.0

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _is_policy_sender_alive_unlocked(self) -> bool:
        if self._user_stopped:
            return False
        if self._policy_resume_armed:
            return True
        # Fallback: if stream is policy-stopped and we still have a correlation-id,
        # keep resume eligibility to avoid missing resume due to transient flag drift.
        return bool(
            self._status.state == "stopped_by_policy" and self._active_correlation_id
        )

    def _status_payload_unlocked(self) -> dict:
        result = self._status.to_dict()
        result["owner_device_id"] = self._active_device_id
        result["owner_device_name"] = self._active_device_name
        result["preferred_device_id"] = self._preferred_device_id
        result["preferred_device_name"] = self._preferred_device_name
        result["command_status"] = self._command_status
        result["command_error"] = self._command_error
        result["desired_stream_state"] = "on" if self._desired_stream_on else "off"
        return result

    def _is_agent_online_unlocked(self, device_id: Optional[str]) -> bool:
        if not device_id:
            return False
        meta = self._agent_registry.get(device_id)
        if not meta:
            return False
        last_seen = float(meta.get("last_seen_at") or 0.0)
        if time.time() - last_seen <= AGENT_ONLINE_TTL_SECONDS:
            return True
        self._agent_registry.pop(device_id, None)
        return False

    def _online_agent_ids_unlocked(self) -> list[str]:
        now = time.time()
        stale_ids = []
        online = []
        for device_id, meta in self._agent_registry.items():
            last_seen = float(meta.get("last_seen_at") or 0.0)
            if now - last_seen <= AGENT_ONLINE_TTL_SECONDS:
                online.append((device_id, last_seen))
            else:
                stale_ids.append(device_id)
        for stale_id in stale_ids:
            self._agent_registry.pop(stale_id, None)
        online.sort(key=lambda item: item[1], reverse=True)
        return [device_id for device_id, _ in online]

    def _mark_preferred_device_unlocked(
        self,
        *,
        device_id: Optional[str],
        device_name: Optional[str] = None,
    ) -> None:
        normalized_id = device_id.strip() if isinstance(device_id, str) else ""
        if not normalized_id:
            return
        normalized_name = (
            device_name.strip() if isinstance(device_name, str) else ""
        )
        if not normalized_name:
            meta = self._agent_registry.get(normalized_id) or {}
            normalized_name = str(meta.get("device_name") or "").strip()
        if not normalized_name and self._preferred_device_id == normalized_id:
            normalized_name = str(self._preferred_device_name or "").strip()
        if not normalized_name:
            normalized_name = normalized_id
        self._preferred_device_id = normalized_id
        self._preferred_device_name = normalized_name

    def _select_panel_target_device_unlocked(
        self,
        *,
        should_stream: bool,
        requested_device_id: Optional[str] = None,
    ) -> tuple[Optional[str], Optional[str]]:
        active_states = {"live", "paused_for_announcement", "stopped_by_policy"}
        if requested_device_id:
            if self._is_agent_online_unlocked(requested_device_id):
                return requested_device_id, None
            return None, "no_agent_available"

        # Deterministic control: prefer current owner while stream is active.
        if self._status.state in active_states and self._active_device_id:
            return self._active_device_id, None

        # Panel stop while idle/error is an idempotent no-op.
        if not should_stream and self._status.state in {"idle", "error"}:
            return None, "noop"

        if not self._preferred_device_id:
            return None, "preferred_device_not_set"
        if not self._is_agent_online_unlocked(self._preferred_device_id):
            return None, "preferred_device_offline"
        return self._preferred_device_id, None

    def _new_command_id_unlocked(self) -> str:
        return f"cmd-{self._desired_generation}-{int(time.time() * 1000)}"

    def _set_desired_state_unlocked(
        self,
        *,
        should_stream: bool,
        target_device_id: str,
        issued_by: str,
    ) -> dict:
        self._desired_generation += 1
        self._desired_stream_on = bool(should_stream)
        self._desired_target_device_id = target_device_id
        self._desired_updated_at = time.time()
        self._active_command_id = self._new_command_id_unlocked()
        self._command_status = "pending"
        self._command_error = None
        action = "start_stream" if should_stream else "stop_stream"
        command = {
            "id": self._active_command_id,
            "generation": self._desired_generation,
            "action": action,
            "target_device_id": target_device_id,
            "issued_at": _utc_iso(self._desired_updated_at),
            "expires_at": _utc_iso(self._desired_updated_at + COMMAND_TTL_SECONDS),
        }
        log_system(
            "stream_desired_state_updated",
            {
                "desired_state": "on" if should_stream else "off",
                "generation": self._desired_generation,
                "target_device_id": target_device_id,
                "command_id": self._active_command_id,
                "issued_by": issued_by,
            },
        )
        return command

    def _build_control_envelope_unlocked(
        self,
        *,
        request_device_id: Optional[str],
    ) -> dict:
        now = time.time()
        if (
            self._command_status == "pending"
            and self._desired_updated_at > 0
            and now - self._desired_updated_at > COMMAND_TTL_SECONDS
        ):
            self._command_status = "expired"
            self._command_error = "command_ttl_expired"
            log_error(
                "stream_desired_command_expired",
                {
                    "command_id": self._active_command_id,
                    "generation": self._desired_generation,
                    "target_device_id": self._desired_target_device_id,
                },
            )

        command = None
        if (
            request_device_id
            and self._command_status == "pending"
            and self._active_command_id
            and self._desired_target_device_id
            and request_device_id == self._desired_target_device_id
        ):
            meta = self._agent_registry.get(request_device_id) or {}
            last_applied_generation = int(meta.get("last_applied_generation") or 0)
            if last_applied_generation < self._desired_generation:
                command = {
                    "id": self._active_command_id,
                    "generation": self._desired_generation,
                    "action": "start_stream" if self._desired_stream_on else "stop_stream",
                    "target_device_id": self._desired_target_device_id,
                    "issued_at": _utc_iso(self._desired_updated_at),
                    "expires_at": _utc_iso(self._desired_updated_at + COMMAND_TTL_SECONDS),
                }

        return {
            "desired_generation": self._desired_generation,
            "desired_stream_state": "on" if self._desired_stream_on else "off",
            "target_device_id": self._desired_target_device_id,
            "command_status": self._command_status,
            "command_error": self._command_error,
            "command": command,
        }

    def request_remote_state(
        self,
        *,
        should_stream: bool,
        issued_by: str = "panel",
        target_device_id: Optional[str] = None,
    ) -> dict:
        with self._lock:
            target, selection_error = self._select_panel_target_device_unlocked(
                should_stream=should_stream,
                requested_device_id=target_device_id,
            )
            if selection_error == "noop":
                control = self._build_control_envelope_unlocked(request_device_id=None)
                return {
                    "success": True,
                    "status": self._status_payload_unlocked(),
                    "control": control,
                    "noop": True,
                }
            if selection_error:
                return {
                    "success": False,
                    "error": selection_error,
                    "status": self._status_payload_unlocked(),
                }

            if (
                self._command_status == "pending"
                and self._desired_target_device_id == target
                and self._desired_stream_on == bool(should_stream)
            ):
                control = self._build_control_envelope_unlocked(request_device_id=target)
                return {
                    "success": True,
                    "status": self._status_payload_unlocked(),
                    "control": control,
                }

            command = self._set_desired_state_unlocked(
                should_stream=should_stream,
                target_device_id=target,
                issued_by=issued_by,
            )
            control = self._build_control_envelope_unlocked(request_device_id=target)
            return {
                "success": True,
                "status": self._status_payload_unlocked(),
                "control": control,
                "command": command,
            }

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start(
        self,
        correlation_id: Optional[str] = None,
        device_id: Optional[str] = None,
        device_name: Optional[str] = None,
    ) -> dict:
        """Start a stream session.

        Same device → idempotent.
        Different device (or no device tracked) → takeover: stop old receiver,
        wait for it to finish (outside the service lock), start new receiver.
        The wait-outside-lock design (Fix 3) ensures that status() and
        heartbeat() callers are never blocked during the takeover wait.

        Returns:
            dict with 'success' and 'status' keys.
            Takeover responses also include 'takeover': True.
        """
        request_correlation_id = (
            correlation_id.strip() if isinstance(correlation_id, str) else ""
        )
        if not request_correlation_id:
            request_correlation_id = _new_correlation_id()
        request_device_id = device_id.strip() if isinstance(device_id, str) else ""
        if not request_device_id:
            request_device_id = None
        request_device_name = device_name.strip() if isinstance(device_name, str) else None

        # ── Phase 1 (inside lock): decide action ──────────────────────
        with self._lock:
            if self._mid_takeover:
                # Another takeover in flight — reject to avoid race.
                logger.info(
                    "StreamService: start rejected, takeover already in progress"
                )
                return {
                    "success": False,
                    "error": "takeover_in_progress",
                    "status": self._status.to_dict(),
                }

            if self._status.active and self._status.state == "live":
                if request_device_id and request_device_id == self._active_device_id:
                    # Same device — idempotent, refresh heartbeat.
                    logger.info(
                        "StreamService: start called but already live for same device (idempotent)"
                    )
                    self._policy_resume_armed = True
                    self._user_stopped = False
                    if request_device_name:
                        self._active_device_name = request_device_name
                    self._mark_preferred_device_unlocked(
                        device_id=request_device_id,
                        device_name=request_device_name,
                    )
                    # Treat an explicit re-start as a heartbeat so the timer resets.
                    if self._last_heartbeat_at > 0:
                        self._last_heartbeat_at = time.monotonic()
                    return {
                        "success": True,
                        "status": self._status.to_dict(),
                        "owner_correlation_id": self._active_correlation_id,
                        "owner_device_id": self._active_device_id,
                    }

                # Different device (or no device_id) — begin takeover.
                inherited_source = self._status.source_before_stream
                logger.info(
                    "StreamService: takeover phase-1 (new_device=%s evicts device=%s cid=%s)",
                    request_device_id or "-",
                    self._active_device_id or "-",
                    request_correlation_id,
                )
                log_system(
                    "stream_takeover_start",
                    {
                        "new_device_id": request_device_id,
                        "evicted_device_id": self._active_device_id,
                        "new_correlation_id": request_correlation_id,
                    },
                )
                if self._manager:
                    self._manager.stop_receiver()
                self._mid_takeover = True
                # Save what we need for phase 3; _status still reflects the old
                # session so concurrent status() callers see a live response.

        # ── Phase 2 (OUTSIDE lock): wait for old receiver to die ───────
        # This keeps _lock free so status(), heartbeat(), is_alive() etc.
        # are never blocked during the (potentially multi-hundred-ms) wait.
        if self._mid_takeover and self._manager:
            self._manager.wait_for_stop_complete(timeout=1.3)

        # ── Phase 3 (inside lock again): start new receiver ───────────
        if self._mid_takeover:
            with self._lock:
                self._mid_takeover = False
                if not self._user_stopped:
                    # _user_stopped=True means stop() was called while we waited.
                    if self._manager and not self._manager.start_receiver(
                        correlation_id=request_correlation_id
                    ):
                        self._status = StreamStatus(
                            active=False,
                            state="error",
                            source_before_stream=inherited_source,
                            last_error="receiver_start_failed",
                        )
                        self._active_correlation_id = None
                        self._active_device_id = None
                        self._active_device_name = None
                        self._policy_resume_armed = False
                        self._last_heartbeat_at = 0.0
                        log_error(
                            "stream_takeover_failed",
                            {
                                "reason": "receiver_start_failed",
                                "new_device_id": request_device_id,
                                "correlation_id": request_correlation_id,
                            },
                        )
                        # Fix 1: restore playlist so Pi doesn't stay silent.
                        if inherited_source == "playlist":
                            self._restore_playlist()
                        return {"success": False, "status": self._status.to_dict()}

                    self._status = StreamStatus(
                        active=True,
                        state="live",
                        source_before_stream=inherited_source,
                        last_error=None,
                    )
                    self._session_started_at = time.monotonic()
                    self._reset_xrun_tracking()
                    self._active_correlation_id = request_correlation_id
                    self._active_device_id = request_device_id
                    self._active_device_name = request_device_name
                    self._policy_resume_armed = True
                    self._user_stopped = False
                    if request_device_id:
                        self._mark_preferred_device_unlocked(
                            device_id=request_device_id,
                            device_name=request_device_name,
                        )
                        self._desired_stream_on = True
                        self._desired_target_device_id = request_device_id
                    self._command_status = "idle"
                    self._command_error = None
                    self._active_command_id = None
                    # Panel starts (no device_id) never send heartbeats; keep
                    # the monitor dormant so the stream isn't auto-stopped.
                    self._last_heartbeat_at = time.monotonic() if request_device_id else 0.0
                    log_system(
                        "stream_takeover_complete",
                        {
                            "new_device_id": request_device_id,
                            "new_correlation_id": request_correlation_id,
                            "inherited_source": inherited_source,
                        },
                    )
                    return {
                        "success": True,
                        "status": self._status.to_dict(),
                        "takeover": True,
                    }
                else:
                    # stop() was called during phase 2; return current (idle) state.
                    return {"success": False, "status": self._status.to_dict()}

        # ── Normal start (no takeover) ─────────────────────────────────
        # Ensure any background stop has completed before starting.
        if self._manager:
            self._manager.wait_for_stop_complete(timeout=1.3)

        with self._lock:
            try:
                self._user_stopped = False
                player = self._player_fn()
                player_state = player.get_state()
                was_playlist_active = player_state.get("playlist", {}).get(
                    "active", False
                )
                source_before = "playlist" if was_playlist_active else "none"

                was_playing = player_state.get("is_playing", False)
                previous_file = player_state.get("current_file")
                previous_position = player_state.get("position", 0.0) or 0.0

                if was_playlist_active:
                    player.stop_playlist()
                    logger.info("StreamService: stopped playlist for stream")
                elif was_playing:
                    player.stop()
                    logger.info("StreamService: stopped playback for stream")

                if self._manager and not self._manager.start_receiver(
                    correlation_id=request_correlation_id
                ):
                    # Rollback: restore previous playback state
                    if was_playlist_active:
                        self._restore_playlist()
                    elif was_playing and previous_file:
                        try:
                            player.play(previous_file, start_position=previous_position)
                            logger.info("StreamService: restored single-track after failed start")
                        except Exception as play_exc:
                            logger.warning("StreamService: failed to restore single-track: %s", play_exc)
                    self._status = StreamStatus(
                        active=False,
                        state="error",
                        source_before_stream=source_before,
                        last_error="receiver_start_failed",
                    )
                    self._active_correlation_id = None
                    self._active_device_id = None
                    self._active_device_name = None
                    self._policy_resume_armed = False
                    log_error(
                        "stream_start_failed",
                        {
                            "reason": "receiver_start_failed",
                            "correlation_id": request_correlation_id,
                        },
                    )
                    return {"success": False, "status": self._status.to_dict()}

                self._status = StreamStatus(
                    active=True,
                    state="live",
                    source_before_stream=source_before,
                    last_error=None,
                )
                self._session_started_at = time.monotonic()
                self._reset_xrun_tracking()
                self._active_correlation_id = request_correlation_id
                self._active_device_id = request_device_id
                self._active_device_name = request_device_name
                self._policy_resume_armed = True
                if request_device_id:
                    self._mark_preferred_device_unlocked(
                        device_id=request_device_id,
                        device_name=request_device_name,
                    )
                    self._desired_stream_on = True
                    self._desired_target_device_id = request_device_id
                self._command_status = "idle"
                self._command_error = None
                self._active_command_id = None
                # Panel starts (no device_id) never send heartbeats; keep
                # the monitor dormant so the stream isn't auto-stopped.
                self._last_heartbeat_at = time.monotonic() if request_device_id else 0.0
                log_system(
                    "stream_started",
                    {
                        "source_before": source_before,
                        "correlation_id": request_correlation_id,
                    },
                )
                return {"success": True, "status": self._status.to_dict()}

            except Exception as exc:
                logger.error("StreamService: start failed: %s", exc)
                self._status = StreamStatus(
                    active=False,
                    state="error",
                    last_error=str(exc),
                )
                self._active_correlation_id = None
                self._active_device_id = None
                self._active_device_name = None
                self._policy_resume_armed = False
                log_error(
                    "stream_start_exception",
                    {
                        "error": str(exc),
                        "correlation_id": request_correlation_id,
                    },
                )
                return {"success": False, "status": self._status.to_dict()}

    def stop(self) -> dict:
        """Stop the active stream session (idempotent).

        Returns:
            dict with 'success' and 'status' keys.
        """
        with self._lock:
            # If a takeover is in flight between phase 1 and 3, signal it to abort.
            self._mid_takeover = False

            if not self._status.active and self._status.state == "idle":
                logger.info(
                    "StreamService: stop called but already idle (idempotent)"
                )
                self._policy_resume_armed = False
                self._user_stopped = True
                self._active_correlation_id = None
                self._active_device_id = None
                self._active_device_name = None
                self._reset_xrun_tracking()
                return {"success": True, "status": self._status.to_dict()}

            try:
                self._policy_resume_armed = False
                self._user_stopped = True
                source_before = self._status.source_before_stream
                correlation_id = self._active_correlation_id
                stop_error = None

                if self._manager:
                    if not self._manager.stop_receiver():
                        stop_error = "receiver_stop_failed"
                        logger.warning(
                            "StreamService: stop_receiver returned False, "
                            "receiver may still be running"
                        )

                if source_before == "playlist":
                    self._restore_playlist()

                self._status = StreamStatus(
                    active=False,
                    state="error" if stop_error else "idle",
                    source_before_stream="none",
                    last_error=stop_error,
                )
                session_duration = round(
                    time.monotonic() - self._session_started_at, 1
                ) if self._session_started_at > 0 else 0.0
                log_system(
                    "stream_stopped",
                    {
                        "restored_source": source_before,
                        "correlation_id": correlation_id,
                        "session_duration_seconds": session_duration,
                        "device_id": self._active_device_id,
                        "device_name": self._active_device_name,
                    },
                )
                self._session_started_at = 0.0
                self._active_correlation_id = None
                self._active_device_id = None
                self._active_device_name = None
                self._last_heartbeat_at = 0.0
                self._desired_stream_on = False
                self._command_status = "idle"
                self._command_error = None
                self._active_command_id = None
                self._reset_xrun_tracking()
                return {"success": True, "status": self._status.to_dict()}

            except Exception as exc:
                logger.error("StreamService: stop failed: %s", exc)
                self._status = StreamStatus(
                    active=False,
                    state="error",
                    last_error=str(exc),
                )
                log_error(
                    "stream_stop_exception",
                    {
                        "error": str(exc),
                        "correlation_id": self._active_correlation_id,
                    },
                )
                self._active_correlation_id = None
                self._active_device_id = None
                self._active_device_name = None
                return {"success": False, "status": self._status.to_dict()}

    def status(self) -> dict:
        """Get current stream status.

        Returns:
            StreamStatus as dict, extended with 'owner_device_id'.
        """
        with self._lock:
            if (
                self._status.active
                and self._status.state == "live"
                and not self._mid_takeover
                and self._manager
                and not self._manager.is_alive()
            ):
                correlation_id = self._active_correlation_id
                owner_device_id = self._active_device_id
                self._status = StreamStatus(
                    active=False,
                    state="error",
                    source_before_stream=self._status.source_before_stream,
                    last_error="receiver_died",
                )
                self._policy_resume_armed = False
                self._active_correlation_id = None
                self._active_device_id = None
                self._active_device_name = None
                log_error(
                    "stream_receiver_died",
                    {
                        "reason": "receiver_died",
                        "correlation_id": correlation_id,
                        "owner_device_id": owner_device_id,
                    },
                )
            return self._status_payload_unlocked()

    def heartbeat(
        self,
        device_id: Optional[str] = None,
        device_name: Optional[str] = None,
        *,
        last_applied_generation: Optional[int] = None,
        last_command_id: Optional[str] = None,
        last_command_result: Optional[str] = None,
        last_command_error: Optional[str] = None,
        sender_running: Optional[bool] = None,
    ) -> dict:
        """Process agent heartbeat and return control-plane envelope.

        The same endpoint now carries:
        - Keepalive acceptance/rejection for active stream ownership
        - Desired-state command envelope for remote start/stop
        - Agent command-ack metadata (generation/result/error)
        """
        with self._lock:
            request_device_id = (
                device_id.strip() if isinstance(device_id, str) else ""
            ) or None
            request_device_name = (
                device_name.strip() if isinstance(device_name, str) else None
            )
            now_epoch = time.time()

            if request_device_id:
                meta = self._agent_registry.get(request_device_id)
                if meta is None:
                    meta = {
                        "device_id": request_device_id,
                        "device_name": request_device_name or request_device_id,
                        "last_seen_at": now_epoch,
                        "last_applied_generation": 0,
                        "last_command_id": None,
                        "last_command_result": None,
                        "last_command_error": None,
                        "sender_running": None,
                    }
                    self._agent_registry[request_device_id] = meta
                meta["last_seen_at"] = now_epoch
                if request_device_name:
                    meta["device_name"] = request_device_name
                    if request_device_id == self._preferred_device_id:
                        self._preferred_device_name = request_device_name
                if isinstance(last_applied_generation, int):
                    meta["last_applied_generation"] = max(
                        int(meta.get("last_applied_generation") or 0),
                        int(last_applied_generation),
                    )
                if isinstance(last_command_id, str) and last_command_id.strip():
                    meta["last_command_id"] = last_command_id.strip()
                if isinstance(last_command_result, str) and last_command_result.strip():
                    meta["last_command_result"] = last_command_result.strip().lower()
                if isinstance(last_command_error, str) and last_command_error.strip():
                    meta["last_command_error"] = last_command_error.strip()
                if isinstance(sender_running, bool):
                    meta["sender_running"] = sender_running

                if (
                    self._active_command_id
                    and meta.get("last_command_id") == self._active_command_id
                    and meta.get("last_applied_generation", 0) >= self._desired_generation
                ):
                    cmd_result = str(meta.get("last_command_result") or "").lower()
                    if cmd_result == "applied":
                        self._command_status = "applied"
                        self._command_error = None
                    elif cmd_result == "failed":
                        self._command_status = "failed"
                        self._command_error = str(meta.get("last_command_error") or "agent_command_failed")

            accepted = False
            reason = "no_active_stream"
            active_states = {"live", "paused_for_announcement", "stopped_by_policy"}
            if self._status.state in active_states:
                if self._active_device_id is not None and request_device_id != self._active_device_id:
                    accepted = False
                    reason = "not_owner"
                else:
                    accepted = True
                    reason = None
                    self._last_heartbeat_at = time.monotonic()
                    if request_device_name:
                        self._active_device_name = request_device_name

            control = self._build_control_envelope_unlocked(
                request_device_id=request_device_id
            )
            result = {
                "accepted": accepted,
                "reason": reason,
                "status": self._status_payload_unlocked(),
                "control": control,
            }
            if reason == "not_owner":
                result["owner_device_id"] = self._active_device_id
            return result

    def policy_sender_alive(self) -> bool:
        """Policy-level sender liveness for Faz 4 runtime rules."""
        with self._lock:
            return self._is_policy_sender_alive_unlocked()

    def pause_for_announcement(self) -> dict:
        """Temporarily pause live stream for announcement playback."""
        with self._lock:
            self._mid_takeover = False
            
            if self._status.state == "paused_for_announcement":
                return {"success": True, "status": self._status.to_dict()}
            if not self._status.active or self._status.state != "live":
                return {"success": True, "status": self._status.to_dict()}

            if self._manager and not self._manager.stop_receiver():
                logger.warning(
                    "StreamService: pause_for_announcement stop_receiver returned False"
                )

            self._status = StreamStatus(
                active=True,
                state="paused_for_announcement",
                source_before_stream=self._status.source_before_stream,
                last_error=None,
            )
            log_system(
                "stream_paused_for_announcement",
                {"correlation_id": self._active_correlation_id},
            )
            return {"success": True, "status": self._status.to_dict()}

    def resume_after_announcement(self) -> dict:
        """Resume stream after announcement if still policy-eligible."""
        with self._lock:
            if self._status.state != "paused_for_announcement":
                return {"success": True, "status": self._status.to_dict()}
            if not self._is_policy_sender_alive_unlocked():
                self._status = StreamStatus(
                    active=False,
                    state="idle",
                    source_before_stream=self._status.source_before_stream,
                    last_error=None,
                )
                self._policy_resume_armed = False
                self._active_correlation_id = None
                self._active_device_id = None
                self._active_device_name = None
                return {"success": True, "status": self._status.to_dict()}

            if self._manager and not self._manager.start_receiver(
                correlation_id=self._active_correlation_id
            ):
                self._status = StreamStatus(
                    active=False,
                    state="error",
                    source_before_stream=self._status.source_before_stream,
                    last_error="receiver_start_failed",
                )
                return {"success": False, "status": self._status.to_dict()}

            self._status = StreamStatus(
                active=True,
                state="live",
                source_before_stream=self._status.source_before_stream,
                last_error=None,
            )
            # Reset heartbeat timer so the agent gets a fresh 15-second window
            # after resume. Without this, if the last heartbeat arrived just
            # before the announcement pause, the monitor fires immediately on
            # resume and kills the stream (observed in production 2026-03-18).
            if self._last_heartbeat_at > 0:
                self._last_heartbeat_at = time.monotonic()
            self._reset_xrun_tracking()
            log_system(
                "stream_resumed",
                {
                    "source": "announcement_end",
                    "correlation_id": self._active_correlation_id,
                },
            )
            return {"success": True, "status": self._status.to_dict()}

    def force_stop_by_policy(self) -> dict:
        """Force-stop stream output due to silence policy (intentional, non-error)."""
        with self._lock:
            self._mid_takeover = False
            
            if self._status.state == "stopped_by_policy" and not self._status.active:
                return {"success": True, "status": self._status.to_dict()}
            if not self._status.active and self._status.state != "paused_for_announcement":
                return {"success": True, "status": self._status.to_dict()}

            if self._manager and not self._manager.stop_receiver():
                logger.warning(
                    "StreamService: force_stop_by_policy stop_receiver returned False"
                )

            self._status = StreamStatus(
                active=False,
                state="stopped_by_policy",
                source_before_stream=self._status.source_before_stream,
                last_error=None,
            )
            self._policy_resume_armed = True
            self._user_stopped = False
            log_system(
                "stream_force_stopped_by_policy",
                {"correlation_id": self._active_correlation_id},
            )
            return {"success": True, "status": self._status.to_dict()}

    def resume_after_policy(self) -> dict:
        """Resume stream when silence policy ends and policy conditions allow it."""
        with self._lock:
            if self._status.state != "stopped_by_policy":
                log_system(
                    "stream_resume_skipped",
                    {
                        "source": "policy_end",
                        "reason": "state_not_stopped_by_policy",
                        "state": self._status.state,
                        "active": self._status.active,
                        "policy_resume_armed": self._policy_resume_armed,
                        "user_stopped": self._user_stopped,
                        "correlation_id": self._active_correlation_id,
                    },
                )
                return {"success": True, "status": self._status.to_dict()}
            if not self._is_policy_sender_alive_unlocked():
                reason = "user_stopped" if self._user_stopped else "sender_not_alive"
                log_system(
                    "stream_resume_skipped",
                    {
                        "source": "policy_end",
                        "reason": reason,
                        "state": self._status.state,
                        "active": self._status.active,
                        "policy_resume_armed": self._policy_resume_armed,
                        "user_stopped": self._user_stopped,
                        "correlation_id": self._active_correlation_id,
                    },
                )
                return {"success": True, "status": self._status.to_dict()}

            if self._manager and not self._manager.start_receiver(
                correlation_id=self._active_correlation_id
            ):
                self._status = StreamStatus(
                    active=False,
                    state="error",
                    source_before_stream=self._status.source_before_stream,
                    last_error="receiver_start_failed",
                )
                log_error(
                    "stream_resume_failed",
                    {
                        "source": "policy_end",
                        "reason": "receiver_start_failed",
                        "correlation_id": self._active_correlation_id,
                    },
                )
                return {"success": False, "status": self._status.to_dict()}

            self._status = StreamStatus(
                active=True,
                state="live",
                source_before_stream=self._status.source_before_stream,
                last_error=None,
            )
            self._reset_xrun_tracking()
            log_system(
                "stream_resumed",
                {"source": "policy_end", "correlation_id": self._active_correlation_id},
            )
            return {"success": True, "status": self._status.to_dict()}

    def _restore_playlist(self):
        """Restore playlist state from DB after stream stop."""
        try:
            import database as db

            player = self._player_fn()
            db_state = db.get_playlist_state()
            if db_state and db_state.get("playlist"):
                player.set_playlist(
                    db_state["playlist"],
                    loop=db_state.get("loop", True),
                )
                player.play_playlist()
                logger.info("StreamService: restored playlist after stream stop")
        except Exception as exc:
            logger.warning("StreamService: failed to restore playlist: %s", exc)


_stream_service_singleton: Optional[StreamService] = None
_stream_service_singleton_lock = threading.Lock()


def get_stream_service(stream_manager=None, player_fn=None) -> StreamService:
    """Return singleton StreamService shared by routes and scheduler."""
    global _stream_service_singleton
    with _stream_service_singleton_lock:
        if _stream_service_singleton is None:
            if stream_manager is None:
                from stream_manager import StreamManager

                stream_manager = StreamManager()
            _stream_service_singleton = StreamService(
                stream_manager=stream_manager,
                player_fn=player_fn,
            )
        return _stream_service_singleton
