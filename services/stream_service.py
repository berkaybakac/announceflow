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

    def __init__(self, stream_manager=None):
        self._manager = stream_manager
        self._status = StreamStatus()

    def start(self) -> dict:
        """Start a stream session (idempotent).

        Returns:
            dict with 'success' and 'status' keys.
        """
        # TODO(Faz 3): Implement session start orchestration
        logger.info("StreamService: start stub called")
        return {"success": False, "status": self._status.to_dict()}

    def stop(self) -> dict:
        """Stop the active stream session (idempotent).

        Returns:
            dict with 'success' and 'status' keys.
        """
        # TODO(Faz 3): Implement session stop orchestration
        logger.info("StreamService: stop stub called")
        return {"success": False, "status": self._status.to_dict()}

    def status(self) -> dict:
        """Get current stream status.

        Returns:
            StreamStatus as dict.
        """
        return self._status.to_dict()
