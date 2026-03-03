"""
AnnounceFlow - Stream Manager
Receiver process lifecycle management for LAN audio streaming.

Responsibilities:
- Start/stop the receiver process on Pi4
- Report receiver liveness (is_alive)
- Process isolation: no business logic here

V1 scope: single receiver, same LAN, process-based.
"""
import logging

logger = logging.getLogger(__name__)


class StreamManager:
    """Manages the stream receiver process lifecycle."""

    def __init__(self):
        self._process = None

    def start_receiver(self) -> bool:
        """Start the stream receiver process.

        Returns:
            True if receiver started (or was already running), False on error.
        """
        if self.is_alive():
            return True
        # TODO(Faz 3): Implement receiver process start
        logger.info("StreamManager: start_receiver stub called")
        return False

    def stop_receiver(self) -> bool:
        """Stop the stream receiver process.

        Returns:
            True if receiver stopped (or was already stopped), False on error.
        """
        if not self.is_alive():
            return True
        # TODO(Faz 3): Implement receiver process stop
        logger.info("StreamManager: stop_receiver stub called")
        return True

    def is_alive(self) -> bool:
        """Check if the receiver process is currently running.

        Returns:
            True if receiver process is active, False otherwise.
        """
        return self._process is not None
