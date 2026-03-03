"""
AnnounceFlow - Stream Client (Agent Side)
Sender process lifecycle for LAN audio streaming.

Responsibilities:
- Start/stop the local audio capture + send process
- Report sender liveness
- No UI logic here (UI handled by agent.py)

V1 scope: Windows agent, same LAN, single sender.
"""
import logging

logger = logging.getLogger(__name__)


class StreamClient:
    """Manages the stream sender process on the agent (Windows) side."""

    def __init__(self):
        self._process = None

    def start_sender(self, target_host: str, target_port: int) -> bool:
        """Start capturing and sending local audio to the Pi4 receiver.

        Args:
            target_host: Pi4 IP address or hostname.
            target_port: Port the receiver is listening on.

        Returns:
            True if sender started (or was already running), False on error.
        """
        if self.is_alive():
            return True
        # TODO(Faz 5): Implement sender process start
        logger.info(
            "StreamClient: start_sender stub called (target=%s:%d)",
            target_host,
            target_port,
        )
        return False

    def stop_sender(self) -> bool:
        """Stop the sender process.

        Returns:
            True if sender stopped (or was already stopped), False on error.
        """
        if not self.is_alive():
            return True
        # TODO(Faz 5): Implement sender process stop
        logger.info("StreamClient: stop_sender stub called")
        return True

    def is_alive(self) -> bool:
        """Check if the sender process is currently running.

        Returns:
            True if sender process is active, False otherwise.
        """
        return self._process is not None
