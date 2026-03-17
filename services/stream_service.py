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
import threading
import time
from typing import Optional

from logger import log_error, log_system

logger = logging.getLogger(__name__)

# Seconds after the *last heartbeat* before a stream is auto-stopped.
# Monitoring only activates once the first heartbeat() call is received
# (i.e. _last_heartbeat_at > 0), so senders that never call heartbeat
# are never timed out — backward-compatible with old clients.
HEARTBEAT_TIMEOUT = 15.0


def _new_correlation_id() -> str:
    return f"stream-{int(time.time() * 1000)}-{threading.get_ident()}"


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
        # Monotonic timestamp of the last accepted heartbeat.
        # Stays 0.0 until the first heartbeat() call is received so that
        # old clients that never call heartbeat are never auto-stopped.
        self._last_heartbeat_at: float = 0.0
        # Set to True while a takeover is between its two lock sections
        # (old receiver stopped, new one not yet started).  Prevents a
        # concurrent start() from racing into an inconsistent state.
        self._mid_takeover: bool = False
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
                    self._active_correlation_id = request_correlation_id
                    self._active_device_id = request_device_id
                    self._active_device_name = request_device_name
                    self._policy_resume_armed = True
                    self._user_stopped = False
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
                self._active_correlation_id = request_correlation_id
                self._active_device_id = request_device_id
                self._active_device_name = request_device_name
                self._policy_resume_armed = True
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
                log_system(
                    "stream_stopped",
                    {
                        "restored_source": source_before,
                        "correlation_id": correlation_id,
                    },
                )
                self._active_correlation_id = None
                self._active_device_id = None
                self._active_device_name = None
                self._last_heartbeat_at = 0.0
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
            result = self._status.to_dict()
            result["owner_device_id"] = self._active_device_id
            result["owner_device_name"] = self._active_device_name
            return result

    def heartbeat(self, device_id: Optional[str] = None, device_name: Optional[str] = None) -> dict:
        """Record a sender heartbeat to prevent auto-stop.

        Once called at least once, the heartbeat timer is active.  If no
        heartbeat arrives within HEARTBEAT_TIMEOUT (15 s) the stream is
        auto-stopped.  Senders that never call heartbeat are not monitored
        (backward-compatible with old clients).

        Returns:
            dict with 'accepted' bool, optional 'reason', and 'status'.
        """
        with self._lock:
            request_device_id = (
                device_id.strip() if isinstance(device_id, str) else ""
            ) or None

            active_states = {"live", "paused_for_announcement", "stopped_by_policy"}
            if self._status.state not in active_states:
                return {
                    "accepted": False,
                    "reason": "no_active_stream",
                    "status": self._status.to_dict(),
                }

            if self._active_device_id is not None:
                if request_device_id != self._active_device_id:
                    return {
                        "accepted": False,
                        "reason": "not_owner",
                        "owner_device_id": self._active_device_id,
                        "status": self._status.to_dict(),
                    }

            self._last_heartbeat_at = time.monotonic()
            request_device_name = (
                device_name.strip() if isinstance(device_name, str) else None
            )
            if request_device_name:
                self._active_device_name = request_device_name
            return {"accepted": True, "status": self._status.to_dict()}

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
