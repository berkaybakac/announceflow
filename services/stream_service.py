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
from typing import Optional

from logger import log_error, log_system

logger = logging.getLogger(__name__)


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

    def _is_policy_sender_alive_unlocked(self) -> bool:
        return bool(self._policy_resume_armed and not self._user_stopped)

    def start(self) -> dict:
        """Start a stream session (idempotent).

        Returns:
            dict with 'success' and 'status' keys.
        """
        with self._lock:
            if self._status.active and self._status.state == "live":
                logger.info("StreamService: start called but already live (idempotent)")
                self._policy_resume_armed = True
                self._user_stopped = False
                return {"success": True, "status": self._status.to_dict()}

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

                if self._manager and not self._manager.start_receiver():
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
                    self._policy_resume_armed = False
                    log_error(
                        "stream_start_failed", {"reason": "receiver_start_failed"}
                    )
                    return {"success": False, "status": self._status.to_dict()}

                self._status = StreamStatus(
                    active=True,
                    state="live",
                    source_before_stream=source_before,
                    last_error=None,
                )
                self._policy_resume_armed = True
                log_system("stream_started", {"source_before": source_before})
                return {"success": True, "status": self._status.to_dict()}

            except Exception as exc:
                logger.error("StreamService: start failed: %s", exc)
                self._status = StreamStatus(
                    active=False,
                    state="error",
                    last_error=str(exc),
                )
                self._policy_resume_armed = False
                log_error("stream_start_exception", {"error": str(exc)})
                return {"success": False, "status": self._status.to_dict()}

    def stop(self) -> dict:
        """Stop the active stream session (idempotent).

        Returns:
            dict with 'success' and 'status' keys.
        """
        with self._lock:
            if not self._status.active and self._status.state == "idle":
                logger.info(
                    "StreamService: stop called but already idle (idempotent)"
                )
                self._policy_resume_armed = False
                self._user_stopped = True
                return {"success": True, "status": self._status.to_dict()}

            try:
                self._policy_resume_armed = False
                self._user_stopped = True
                source_before = self._status.source_before_stream
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
                log_system("stream_stopped", {"restored_source": source_before})
                return {"success": True, "status": self._status.to_dict()}

            except Exception as exc:
                logger.error("StreamService: stop failed: %s", exc)
                self._status = StreamStatus(
                    active=False,
                    state="error",
                    last_error=str(exc),
                )
                log_error("stream_stop_exception", {"error": str(exc)})
                return {"success": False, "status": self._status.to_dict()}

    def status(self) -> dict:
        """Get current stream status.

        Returns:
            StreamStatus as dict.
        """
        with self._lock:
            if (
                self._status.active
                and self._status.state == "live"
                and self._manager
                and not self._manager.is_alive()
            ):
                self._status = StreamStatus(
                    active=False,
                    state="error",
                    source_before_stream=self._status.source_before_stream,
                    last_error="receiver_died",
                )
                self._policy_resume_armed = False
            return self._status.to_dict()

    def policy_sender_alive(self) -> bool:
        """Policy-level sender liveness for Faz 4 runtime rules."""
        with self._lock:
            return self._is_policy_sender_alive_unlocked()

    def pause_for_announcement(self) -> dict:
        """Temporarily pause live stream for announcement playback."""
        with self._lock:
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
            log_system("stream_paused_for_announcement", {})
            return {"success": True, "status": self._status.to_dict()}

    def resume_after_announcement(self) -> dict:
        """Resume stream after announcement if still policy-eligible."""
        with self._lock:
            if self._status.state != "paused_for_announcement":
                return {"success": True, "status": self._status.to_dict()}
            if not self._is_policy_sender_alive_unlocked():
                return {"success": True, "status": self._status.to_dict()}

            if self._manager and not self._manager.start_receiver():
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
            return {"success": True, "status": self._status.to_dict()}

    def force_stop_by_policy(self) -> dict:
        """Force-stop stream output due to silence policy (intentional, non-error)."""
        with self._lock:
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
            log_system("stream_force_stopped_by_policy", {})
            return {"success": True, "status": self._status.to_dict()}

    def resume_after_policy(self) -> dict:
        """Resume stream when silence policy ends and policy conditions allow it."""
        with self._lock:
            if self._status.state != "stopped_by_policy":
                return {"success": True, "status": self._status.to_dict()}
            if not self._is_policy_sender_alive_unlocked():
                return {"success": True, "status": self._status.to_dict()}

            if self._manager and not self._manager.start_receiver():
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
