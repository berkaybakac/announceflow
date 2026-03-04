"""
AnnounceFlow - Stream Client (Agent Side)
Sender process lifecycle for LAN audio streaming via ffmpeg.

Responsibilities:
- Start/stop ffmpeg audio capture + UDP send process
- Report sender liveness
- Discover Windows loopback audio device
- No UI logic here (UI handled by agent.py)

V1 scope: Windows agent, same LAN, single sender.
Sender: ffmpeg captures system audio (dshow) and sends raw PCM over UDP.
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

# Known loopback device names to try in order
_KNOWN_LOOPBACK_NAMES = [
    "Stereo Mix",
    "CABLE Output (VB-Audio Virtual Cable)",
    "CABLE Output",
    "What U Hear",
]


def _find_vbcable_installer() -> Optional[str]:
    """Return path to bundled VB-Cable installer, or None."""
    if getattr(sys, "frozen", False):
        path = os.path.join(sys._MEIPASS, "VBCABLE_Setup_x64.exe")
        if os.path.isfile(path):
            return path
    # Dev mode: check agent directory
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "VBCABLE_Setup_x64.exe")
    if os.path.isfile(path):
        return path
    return None


def install_vbcable() -> bool:
    """Install VB-Cable silently if bundled installer is available.

    Requires admin privileges (UAC prompt will appear).
    Windows driver verification dialog may also appear once.

    Returns:
        True if installation succeeded, False otherwise.
    """
    installer = _find_vbcable_installer()
    if not installer:
        logger.warning("install_vbcable: installer not found")
        return False
    try:
        import ctypes

        # Request admin elevation via ShellExecuteW (triggers UAC)
        ret = ctypes.windll.shell32.ShellExecuteW(
            None, "runas", installer, "-i -h", None, 0,
        )
        # ShellExecuteW returns > 32 on success
        if ret <= 32:
            logger.error("install_vbcable: elevation failed (ret=%d)", ret)
            return False

        # Wait for install to complete (driver install takes a few seconds)
        time.sleep(5)
        logger.info("install_vbcable: VB-Cable install initiated")
        return True
    except (OSError, AttributeError) as exc:
        # AttributeError: not on Windows (no ctypes.windll)
        logger.error("install_vbcable: failed: %s", exc)
        return False


def _find_ffmpeg() -> str:
    """Return path to ffmpeg binary.

    Frozen EXE: bundled ffmpeg.exe next to the executable.
    Dev mode: ffmpeg from PATH.
    """
    if getattr(sys, "frozen", False):
        bundled = os.path.join(sys._MEIPASS, "ffmpeg.exe")
        if os.path.isfile(bundled):
            return bundled
    return "ffmpeg"


def discover_loopback_device(ffmpeg_bin: str = "ffmpeg") -> Optional[str]:
    """Discover a usable loopback audio device on Windows via ffmpeg.

    Tries known device names first, then parses ffmpeg -list_devices output.

    Returns:
        Device name string or None if no loopback found.
    """
    # Try known names via ffmpeg probe
    for name in _KNOWN_LOOPBACK_NAMES:
        try:
            probe = subprocess.run(
                [
                    ffmpeg_bin,
                    "-f", "dshow",
                    "-i", f"audio={name}",
                    "-t", "0.1",
                    "-f", "null", "-",
                ],
                capture_output=True,
                timeout=5,
            )
            # ffmpeg returns 0 or writes to stderr without "Could not" on success
            stderr_text = probe.stderr.decode("utf-8", errors="replace")
            if "Could not find" not in stderr_text and "no such filter" not in stderr_text:
                logger.info("discover_loopback_device: found '%s'", name)
                return name
        except (OSError, subprocess.TimeoutExpired):
            continue

    # No known loopback device found.
    # We intentionally do NOT fall back to the first audio device
    # because it is usually a microphone, not a loopback capture.
    logger.warning("discover_loopback_device: no loopback device found")
    return None


class StreamClient:
    """Manages the ffmpeg sender process on the agent (Windows) side.

    Follows the same lifecycle pattern as StreamManager (stream_manager.py):
    - threading.Lock for thread safety
    - subprocess.Popen for process isolation
    - Health check after start (50ms)
    - Graceful shutdown: terminate -> wait(3s) -> kill
    """

    def __init__(self):
        self._process: Optional[subprocess.Popen] = None
        self._lock = threading.Lock()
        self.last_error: Optional[str] = None

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
                self.last_error = "no_loopback_device"
                logger.error(
                    "StreamClient: sender command could not be built"
                )
                return False
            try:
                # Log ffmpeg stderr for debugging
                log_dir = os.path.join(
                    os.path.dirname(os.path.abspath(__file__)), "logs"
                )
                os.makedirs(log_dir, exist_ok=True)
                self._stderr_log = open(
                    os.path.join(log_dir, "stream_sender_ffmpeg.log"), "a"
                )
                self._process = subprocess.Popen(
                    cmd,
                    stdout=subprocess.DEVNULL,
                    stderr=self._stderr_log,
                )
                # Brief health check: catch immediate death
                time.sleep(0.05)
                if self._process.poll() is not None:
                    logger.error(
                        "StreamClient: sender died immediately (exit=%d)",
                        self._process.returncode,
                    )
                    self._process = None
                    self._stderr_log.close()
                    self._stderr_log = None
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
                if hasattr(self, "_stderr_log") and self._stderr_log:
                    self._stderr_log.close()
                    self._stderr_log = None
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
                if hasattr(self, "_stderr_log") and self._stderr_log:
                    self._stderr_log.close()
                    self._stderr_log = None

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
        """Build ffmpeg command for audio capture and UDP send.

        Discovers loopback device and constructs ffmpeg dshow capture command.
        Returns empty list if no loopback device found.
        """
        ffmpeg_bin = _find_ffmpeg()
        device = discover_loopback_device(ffmpeg_bin)
        if not device:
            logger.info("StreamClient: no loopback device, attempting VB-Cable install")
            if install_vbcable():
                device = discover_loopback_device(ffmpeg_bin)
            if not device:
                logger.error("StreamClient: no loopback audio device found")
                return []
        return [
            ffmpeg_bin,
            "-y",
            "-f", "dshow",
            "-i", f"audio={device}",
            "-acodec", "pcm_s16le",
            "-ar", "44100",
            "-ac", "1",
            "-f", "s16le",
            f"udp://{target_host}:{target_port}",
        ]
