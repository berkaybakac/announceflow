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
from typing import Optional

logger = logging.getLogger(__name__)

STREAM_RECEIVER_PORT = 5800
STOP_QUICK_WAIT = 0.3
STOP_BG_GRACE_SECONDS = 3
STOP_KILL_WAIT_SECONDS = 1
START_RETRY_DELAY = 0.25


class StreamManager:
    """Manages the stream receiver process lifecycle."""

    def __init__(self, port: int = STREAM_RECEIVER_PORT):
        self._process = None
        self._lock = threading.Lock()
        self._port = port
        self._consecutive_start_failures = 0

    def _record_start_failure_unlocked(
        self,
        *,
        correlation_id: Optional[str],
        exit_code: Optional[int] = None,
    ) -> None:
        """Track consecutive start failures and emit threshold warnings."""
        self._consecutive_start_failures += 1
        fails = self._consecutive_start_failures
        if fails % 3 == 0:
            logger.warning(
                "StreamManager: consecutive start failures=%d (correlation_id=%s, last_exit=%s)",
                fails,
                correlation_id or "-",
                "-" if exit_code is None else exit_code,
            )

    def start_receiver(self, correlation_id: Optional[str] = None) -> bool:
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
                child_env = os.environ.copy()
                if correlation_id:
                    child_env["ANNOUNCEFLOW_STREAM_CORRELATION_ID"] = str(
                        correlation_id
                    )

                self._process = subprocess.Popen(
                    [sys.executable, receiver_script, str(self._port)],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    env=child_env,
                )
                # Brief health check: catch immediate death (port conflict, script error)
                time.sleep(0.05)
                if self._process.poll() is not None:
                    exit_code = self._process.returncode
                    logger.warning(
                        "StreamManager: receiver died immediately (exit=%d, correlation_id=%s), retrying once after %.0fms",
                        exit_code,
                        correlation_id or "-",
                        START_RETRY_DELAY * 1000,
                    )
                    time.sleep(START_RETRY_DELAY)
                    self._process = subprocess.Popen(
                        [sys.executable, receiver_script, str(self._port)],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        env=child_env,
                    )
                    time.sleep(0.05)
                    if self._process.poll() is not None:
                        logger.error(
                            "StreamManager: receiver died on retry (exit=%d, correlation_id=%s)",
                            self._process.returncode,
                            correlation_id or "-",
                        )
                        self._record_start_failure_unlocked(
                            correlation_id=correlation_id,
                            exit_code=self._process.returncode,
                        )
                        self._process = None
                        return False
                logger.info(
                    "StreamManager: receiver started (pid=%d, port=%d, correlation_id=%s)",
                    self._process.pid,
                    self._port,
                    correlation_id or "-",
                )
                self._consecutive_start_failures = 0
                return True
            except (OSError, subprocess.SubprocessError) as exc:
                logger.error("StreamManager: failed to start receiver: %s", exc)
                self._process = None
                self._record_start_failure_unlocked(correlation_id=correlation_id)
                return False

    def stop_receiver(self) -> bool:
        """Stop the stream receiver process.

        Sends SIGTERM and waits briefly. If the process hasn't exited yet,
        a background thread handles the remaining grace period + SIGKILL,
        so the caller (HTTP handler) returns quickly.

        Returns:
            True if receiver stopped (or was already stopped), False on error.
        """
        with self._lock:
            if not self._is_alive_unlocked():
                return True
            proc = self._process
            self._process = None

        try:
            proc.terminate()
            try:
                proc.wait(timeout=STOP_QUICK_WAIT)
                logger.info("StreamManager: receiver stopped (quick)")
            except subprocess.TimeoutExpired:
                logger.info(
                    "StreamManager: receiver still alive after %.1fs, background cleanup started",
                    STOP_QUICK_WAIT,
                )
                t = threading.Thread(
                    target=self._background_kill, args=(proc,), daemon=True
                )
                t.start()
            return True
        except Exception as exc:
            logger.error("StreamManager: error stopping receiver: %s", exc)
            return False

    @staticmethod
    def _background_kill(proc: subprocess.Popen):
        """Wait for process to exit gracefully, then SIGKILL if needed."""
        try:
            proc.wait(timeout=STOP_BG_GRACE_SECONDS)
            logger.info("StreamManager: receiver stopped (background grace)")
        except subprocess.TimeoutExpired:
            logger.warning(
                "StreamManager: receiver did not exit in %.1fs, forcing kill",
                STOP_BG_GRACE_SECONDS,
            )
            proc.kill()
            try:
                proc.wait(timeout=STOP_KILL_WAIT_SECONDS)
            except subprocess.TimeoutExpired:
                logger.error("StreamManager: receiver did not exit after SIGKILL")

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
