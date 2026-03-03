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
import os
import subprocess
import sys
import threading
import time

logger = logging.getLogger(__name__)

STREAM_RECEIVER_PORT = 5800


class StreamManager:
    """Manages the stream receiver process lifecycle."""

    def __init__(self, port: int = STREAM_RECEIVER_PORT):
        self._process = None
        self._lock = threading.Lock()
        self._port = port

    def start_receiver(self) -> bool:
        """Start the stream receiver process.

        Returns:
            True if receiver started (or was already running), False on error.
        """
        with self._lock:
            if self._is_alive_unlocked():
                return True
            try:
                receiver_script = os.path.join(
                    os.path.dirname(os.path.abspath(__file__)),
                    "_stream_receiver.py",
                )
                self._process = subprocess.Popen(
                    [sys.executable, receiver_script, str(self._port)],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                # Brief health check: catch immediate death (port conflict, script error)
                time.sleep(0.05)
                if self._process.poll() is not None:
                    logger.error(
                        "StreamManager: receiver died immediately (exit=%d)",
                        self._process.returncode,
                    )
                    self._process = None
                    return False
                logger.info(
                    "StreamManager: receiver started (pid=%d, port=%d)",
                    self._process.pid,
                    self._port,
                )
                return True
            except (OSError, subprocess.SubprocessError) as exc:
                logger.error("StreamManager: failed to start receiver: %s", exc)
                self._process = None
                return False

    def stop_receiver(self) -> bool:
        """Stop the stream receiver process.

        Returns:
            True if receiver stopped (or was already stopped), False on error.
        """
        with self._lock:
            if not self._is_alive_unlocked():
                return True
            try:
                self._process.terminate()
                try:
                    self._process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    self._process.kill()
                    self._process.wait(timeout=1)
                logger.info("StreamManager: receiver stopped")
                return True
            except Exception as exc:
                logger.error("StreamManager: error stopping receiver: %s", exc)
                return False
            finally:
                self._process = None

    def is_alive(self) -> bool:
        """Check if the receiver process is currently running.

        Returns:
            True if receiver process is active, False otherwise.
        """
        with self._lock:
            return self._is_alive_unlocked()

    def _is_alive_unlocked(self) -> bool:
        """Check liveness without acquiring lock (caller must hold lock)."""
        if self._process is None:
            return False
        if self._process.poll() is not None:
            self._process = None
            return False
        return True
