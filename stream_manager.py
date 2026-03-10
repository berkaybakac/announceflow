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

from logger import log_system

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
        self._stopping_proc = None
        self._lock = threading.Lock()
        self._port = port
        self._consecutive_start_failures = 0

    @staticmethod
    def _read_stderr_snippet(proc: subprocess.Popen, max_bytes: int = 1024) -> str:
        """Read up to max_bytes from a dead process's stderr pipe."""
        try:
            if proc.stderr:
                raw = proc.stderr.read(max_bytes)
                if raw:
                    return raw.decode("utf-8", errors="replace").strip()[:500]
        except Exception:
            pass
        return ""

    @staticmethod
    def _start_stderr_drain(proc: subprocess.Popen) -> None:
        """Drain stderr in a background thread to prevent pipe buffer deadlock."""
        def _drain():
            try:
                if proc.stderr:
                    for line in iter(proc.stderr.readline, b""):
                        try:
                            msg = line.decode("utf-8", errors="replace").strip()
                            if msg:
                                logger.warning("StreamReceiver[%s]: %s", getattr(proc, "pid", "?"), msg)
                        except Exception:
                            pass
            except Exception:
                pass
        t = threading.Thread(target=_drain, daemon=True)
        t.name = "stream-stderr-drain"
        t.start()

    def _log_stop_reason(
        self,
        reason: str,
        *,
        proc: Optional[subprocess.Popen] = None,
        phase: Optional[str] = None,
        error: Optional[str] = None,
    ) -> None:
        """Emit structured stop telemetry for diagnostics."""
        data = {"reason": reason, "port": self._port}
        if proc is not None:
            data["pid"] = getattr(proc, "pid", None)
        if phase:
            data["phase"] = phase
        if error:
            data["error"] = error
        try:
            log_system("stream_receiver_stop_reason", data)
        except Exception as exc:
            logger.debug("StreamManager: failed to emit stop reason telemetry: %s", exc)

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

    def start_receiver(
        self,
        correlation_id: Optional[str] = None,
        wait_for_stop: bool = False,
    ) -> bool:
        """Start the stream receiver process.

        Args:
            correlation_id: Opaque ID passed to the receiver for telemetry.
            wait_for_stop: If True and a previous receiver stop is still in
                progress, wait up to 1 s for it to finish (then force-kill)
                instead of rejecting the start.  Prefer calling
                wait_for_stop_complete() first (outside any service lock) and
                then calling this with wait_for_stop=False for better
                concurrency.

        Returns:
            True if receiver started (or was already running), False on error.
        """
        with self._lock:
            if self._stopping_proc is not None:
                if self._stopping_proc.poll() is None:
                    if wait_for_stop:
                        try:
                            self._stopping_proc.wait(timeout=1.0)
                        except subprocess.TimeoutExpired:
                            logger.warning(
                                "StreamManager: stopping proc did not exit in 1s, killing (correlation_id=%s)",
                                correlation_id or "-",
                            )
                            self._stopping_proc.kill()
                            try:
                                self._stopping_proc.wait(timeout=0.3)
                            except subprocess.TimeoutExpired:
                                pass
                    else:
                        logger.warning(
                            "StreamManager: receiver stop still in progress, rejecting start (correlation_id=%s)",
                            correlation_id or "-",
                        )
                        return False
                self._stopping_proc = None
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
                    stdin=subprocess.PIPE,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.PIPE,
                    env=child_env,
                )
                # Brief health check: catch immediate death (port conflict, script error)
                time.sleep(0.05)
                if self._process.poll() is not None:
                    exit_code = self._process.returncode
                    stderr_snippet = self._read_stderr_snippet(self._process)
                    logger.warning(
                        "StreamManager: receiver died immediately (exit=%s, correlation_id=%s, stderr=%s), retrying once after %.0fms",
                        exit_code,
                        correlation_id or "-",
                        stderr_snippet,
                        START_RETRY_DELAY * 1000,
                    )
                    self._process = None
                    time.sleep(START_RETRY_DELAY)
                    self._process = subprocess.Popen(
                        [sys.executable, receiver_script, str(self._port)],
                        stdin=subprocess.PIPE,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.PIPE,
                        env=child_env,
                    )
                    time.sleep(0.05)
                    if self._process.poll() is not None:
                        stderr_snippet = self._read_stderr_snippet(self._process)
                        logger.error(
                            "StreamManager: receiver died on retry (exit=%s, correlation_id=%s, stderr=%s)",
                            self._process.returncode,
                            correlation_id or "-",
                            stderr_snippet,
                        )
                        self._record_start_failure_unlocked(
                            correlation_id=correlation_id,
                            exit_code=self._process.returncode,
                        )
                        self._process = None
                        return False
                # Receiver alive — drain stderr in background to prevent pipe buffer deadlock
                self._start_stderr_drain(self._process)
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
                if self._stopping_proc is not None and self._stopping_proc.poll() is None:
                    self._log_stop_reason(
                        "already_stopping",
                        proc=self._stopping_proc,
                    )
                    return True
                if self._stopping_proc is not None and self._stopping_proc.poll() is not None:
                    self._stopping_proc = None
                self._log_stop_reason("already_stopped")
                return True
            proc = self._process
            self._process = None
            self._stopping_proc = proc

        try:
            # Close stdin first — receiver's signal handler sends 'q' to ffmpeg.
            # Closing stdin from manager side is a secondary safety net.
            try:
                if proc.stdin and not proc.stdin.closed:
                    proc.stdin.close()
            except (OSError, BrokenPipeError):
                pass
            proc.terminate()
            try:
                proc.wait(timeout=STOP_QUICK_WAIT)
                logger.info("StreamManager: receiver stopped (quick)")
                self._log_stop_reason("graceful", proc=proc, phase="quick")
                with self._lock:
                    if self._stopping_proc is proc:
                        self._stopping_proc = None
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
            self._log_stop_reason("error", proc=proc, error=str(exc))
            with self._lock:
                if self._stopping_proc is proc:
                    self._stopping_proc = None
            return False

    def wait_for_stop_complete(self, timeout: float = 1.3) -> None:
        """Wait for a pending stop operation to finish.

        Uses short-poll intervals (50 ms) so the caller's service lock need
        not be held during the wait — other threads can call is_alive() and
        status() freely between polls.

        Call this from the takeover path AFTER releasing StreamService._lock
        and BEFORE re-acquiring it to start the new receiver.

        Args:
            timeout: Maximum seconds to wait before force-killing.
        """
        deadline = time.monotonic() + timeout
        while True:
            with self._lock:
                if self._stopping_proc is None:
                    return
                if self._stopping_proc.poll() is not None:
                    self._stopping_proc = None
                    return
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            time.sleep(min(0.05, remaining))
        # Timed out — force-kill so start_receiver() can proceed.
        with self._lock:
            if self._stopping_proc is not None and self._stopping_proc.poll() is None:
                logger.warning(
                    "StreamManager: forcing kill after wait_for_stop_complete timeout (%.1fs)",
                    timeout,
                )
                self._stopping_proc.kill()
                try:
                    self._stopping_proc.wait(timeout=0.3)
                except subprocess.TimeoutExpired:
                    pass
            self._stopping_proc = None

    def _background_kill(self, proc: subprocess.Popen):
        """Wait for process to exit gracefully, then SIGKILL if needed."""
        try:
            proc.wait(timeout=STOP_BG_GRACE_SECONDS)
            logger.info("StreamManager: receiver stopped (background grace)")
            self._log_stop_reason("graceful", proc=proc, phase="background")
        except subprocess.TimeoutExpired:
            logger.warning(
                "StreamManager: receiver did not exit in %.1fs, forcing kill",
                STOP_BG_GRACE_SECONDS,
            )
            proc.kill()
            try:
                proc.wait(timeout=STOP_KILL_WAIT_SECONDS)
                self._log_stop_reason("force_kill", proc=proc)
            except subprocess.TimeoutExpired:
                logger.error("StreamManager: receiver did not exit after SIGKILL")
                self._log_stop_reason("force_kill_timeout", proc=proc)
        finally:
            with self._lock:
                if self._stopping_proc is proc:
                    self._stopping_proc = None

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
