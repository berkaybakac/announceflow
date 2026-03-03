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

    def start(self) -> dict:
        """Start a stream session (idempotent).

        Returns:
            dict with 'success' and 'status' keys.
        """
        with self._lock:
            if self._status.active and self._status.state == "live":
                logger.info("StreamService: start called but already live (idempotent)")
                return {"success": True, "status": self._status.to_dict()}

            try:
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
                log_system("stream_started", {"source_before": source_before})
                return {"success": True, "status": self._status.to_dict()}

            except Exception as exc:
                logger.error("StreamService: start failed: %s", exc)
                self._status = StreamStatus(
                    active=False,
                    state="error",
                    last_error=str(exc),
                )
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
                return {"success": True, "status": self._status.to_dict()}

            try:
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
            if self._status.active and self._manager and not self._manager.is_alive():
                self._status = StreamStatus(
                    active=False,
                    state="error",
                    source_before_stream=self._status.source_before_stream,
                    last_error="receiver_died",
                )
            return self._status.to_dict()

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
