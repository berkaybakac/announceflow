"""
AnnounceFlow - Stream Client (Agent Side)
WASAPI loopback audio capture and UDP sender.

Captures system audio via WASAPI loopback (no extra drivers needed)
and sends raw PCM over UDP to the Pi4 receiver.

V2: Replaced ffmpeg+VB-Cable with soundcard (WASAPI loopback).
    - No driver installation required
    - No need to change default audio device
    - PC audio continues playing normally
"""
import logging
import socket
import threading
from typing import Optional

logger = logging.getLogger(__name__)

STREAM_SENDER_PORT = 5800

# Audio format must match _stream_receiver.py expectations
_SAMPLE_RATE = 44100
_CHANNELS = 1
_BLOCK_SIZE = 4410  # ~100ms at 44100 Hz


class StreamClient:
    """Manages WASAPI loopback capture and UDP send on the agent side.

    Public API (unchanged from V1):
    - start_sender(target_host, target_port) -> bool
    - stop_sender() -> bool
    - is_alive() -> bool
    - last_error: Optional[str]
    """

    def __init__(self):
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self._running = False
        self.last_error: Optional[str] = None

    def start_sender(self, target_host: str, target_port: int) -> bool:
        """Start capturing system audio and sending to Pi4 via UDP.

        Args:
            target_host: Pi4 IP address or hostname.
            target_port: Port the receiver is listening on.

        Returns:
            True if sender started (or was already running), False on error.
        """
        with self._lock:
            if self._running:
                return True

            # Verify soundcard can find a speaker for loopback
            try:
                import soundcard as sc
                speaker = sc.default_speaker()
                if speaker is None:
                    self.last_error = "no_audio_device"
                    logger.error("StreamClient: no default speaker found")
                    return False
                logger.info(
                    "StreamClient: will capture from '%s' via loopback",
                    speaker.name,
                )
            except Exception as exc:
                self.last_error = "no_audio_device"
                logger.error("StreamClient: soundcard init failed: %s", exc)
                return False

            self._running = True
            self._thread = threading.Thread(
                target=self._capture_loop,
                args=(target_host, target_port),
                daemon=True,
            )
            self._thread.start()

            # Brief health check: let thread start and catch immediate errors
            self._thread.join(timeout=0.3)
            if not self._running:
                # Thread set _running to False → startup failed
                logger.error("StreamClient: capture thread died on startup")
                self._thread = None
                return False

            logger.info(
                "StreamClient: sender started (target=%s:%d)",
                target_host,
                target_port,
            )
            return True

    def stop_sender(self) -> bool:
        """Stop the capture and sender.

        Returns:
            True if sender stopped (or was already stopped).
        """
        with self._lock:
            if not self._running:
                return True
            self._running = False

        # Wait outside lock so capture loop can finish
        if self._thread is not None:
            self._thread.join(timeout=3)
            self._thread = None

        logger.info("StreamClient: sender stopped")
        return True

    def is_alive(self) -> bool:
        """Check if the capture thread is currently running."""
        with self._lock:
            return self._running and self._thread is not None and self._thread.is_alive()

    def _capture_loop(self, host: str, port: int) -> None:
        """Capture system audio via WASAPI loopback and send as UDP packets.

        Audio format: s16le, 44100 Hz, mono — matches _stream_receiver.py.
        """
        sock = None
        try:
            import numpy as np
            import soundcard as sc

            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            speaker = sc.default_speaker()

            with speaker.recorder(
                samplerate=_SAMPLE_RATE,
                channels=_CHANNELS,
                blocksize=_BLOCK_SIZE,
            ) as recorder:
                logger.info("StreamClient: capture loop running")
                while self._running:
                    # soundcard returns float32 in [-1.0, 1.0]
                    data = recorder.record(numframes=_BLOCK_SIZE)
                    # Replace any NaN/Inf with 0, then convert to s16le PCM
                    clean = np.nan_to_num(data, nan=0.0, posinf=1.0, neginf=-1.0)
                    pcm = np.clip(clean * 32767, -32768, 32767).astype(
                        np.dtype("<i2")
                    )
                    sock.sendto(pcm.tobytes(), (host, port))

        except Exception as exc:
            logger.error("StreamClient: capture loop error: %s", exc)
            self.last_error = "capture_error"
        finally:
            if sock is not None:
                sock.close()
            self._running = False
            logger.info("StreamClient: capture loop ended")
