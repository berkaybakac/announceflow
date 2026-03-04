"""
AnnounceFlow - Stream Client (Agent Side)
Sender process lifecycle for LAN audio streaming.

Responsibilities:
- Start/stop the local audio capture + send process
- Report sender liveness
- Provide sender subprocess entry point (run_sender_mode)
- No UI logic here (UI handled by agent.py)

V1 scope: Windows agent, same LAN, single sender.
Sender mode: same EXE with --stream-sender flag (no external Python needed).
"""
import logging
import os
import subprocess
import sys
import threading
import time
from typing import List, Optional

logger = logging.getLogger(__name__)

STREAM_SENDER_PORT = 5800


def run_sender_mode(host: str, port: int) -> None:
    """Entry point for --stream-sender subprocess mode. Placeholder UDP sender.

    Spawned by StreamClient.start_sender() as a subprocess of the same EXE.
    Sends dummy UDP packets until SIGTERM/SIGINT received.

    Args:
        host: Target receiver hostname or IP.
        port: Target receiver UDP port.
    """
    import signal
    import socket

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    running = True

    def _sig(_signum, _frame):
        nonlocal running
        running = False  # noqa: F841 — nonlocal write detected as unused by some linters

    signal.signal(signal.SIGTERM, _sig)
    signal.signal(signal.SIGINT, _sig)

    while running:
        try:
            sock.sendto(b"\x00" * 320, (host, port))
            time.sleep(0.02)  # ~50 pps placeholder
        except OSError:
            break

    sock.close()


class StreamClient:
    """Manages the stream sender process on the agent (Windows) side.

    Follows the same lifecycle pattern as StreamManager (stream_manager.py):
    - threading.Lock for thread safety
    - subprocess.Popen for process isolation
    - Health check after start (50ms)
    - Graceful shutdown: terminate -> wait(3s) -> kill
    """

    def __init__(self):
        self._process: Optional[subprocess.Popen] = None
        self._lock = threading.Lock()

    def start_sender(self, target_host: str, target_port: int) -> bool:
        """Start capturing and sending local audio to the Pi4 receiver.

        Args:
            target_host: Pi4 IP address or hostname.
            target_port: Port the receiver is listening on.

        Returns:
            True if sender started (or was already running), False on error.
        """
        with self._lock:
            if self._is_alive_unlocked():
                return True
            cmd = self._build_sender_cmd(target_host, target_port)
            if not cmd:
                logger.error(
                    "StreamClient: sender command could not be built"
                )
                return False
            try:
                self._process = subprocess.Popen(
                    cmd,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                # Brief health check: catch immediate death
                time.sleep(0.05)
                if self._process.poll() is not None:
                    logger.error(
                        "StreamClient: sender died immediately (exit=%d)",
                        self._process.returncode,
                    )
                    self._process = None
                    return False
                logger.info(
                    "StreamClient: sender started (pid=%d, target=%s:%d)",
                    self._process.pid,
                    target_host,
                    target_port,
                )
                return True
            except (OSError, subprocess.SubprocessError) as exc:
                logger.error("StreamClient: failed to start sender: %s", exc)
                self._process = None
                return False

    def stop_sender(self) -> bool:
        """Stop the sender process.

        Returns:
            True if sender stopped (or was already stopped), False on error.
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
                logger.info("StreamClient: sender stopped")
                return True
            except Exception as exc:
                logger.error("StreamClient: error stopping sender: %s", exc)
                return False
            finally:
                self._process = None

    def is_alive(self) -> bool:
        """Check if the sender process is currently running.

        Returns:
            True if sender process is active, False otherwise.
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

    @staticmethod
    def _build_sender_cmd(target_host: str, target_port: int) -> List[str]:
        """Build subprocess command for sender mode.

        Frozen EXE: runs self with --stream-sender flag.
        Dev mode: runs python agent.py --stream-sender.
        """
        if getattr(sys, "frozen", False):
            return [
                sys.executable,
                "--stream-sender",
                target_host,
                str(target_port),
            ]
        agent_entry = os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            "agent.py",
        )
        return [
            sys.executable,
            agent_entry,
            "--stream-sender",
            target_host,
            str(target_port),
        ]
