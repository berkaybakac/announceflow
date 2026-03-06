"""
AnnounceFlow - Stream Client (Agent Side)
WASAPI loopback audio capture and UDP sender.

Captures system audio via WASAPI loopback (no extra drivers needed)
and sends raw PCM over UDP to the Pi4 receiver.

Current transport uses soundcard-based WASAPI loopback.
    - No extra driver installation required
    - No need to change default audio device
    - PC audio continues playing normally
"""
import json
import logging
import os
import socket
import threading
import time
import traceback
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)
stream_logger = logging.getLogger("agent.stream")

STREAM_SENDER_PORT = 5800

# Audio format must match _stream_receiver.py expectations
_SAMPLE_RATE = 44100
def _env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    if value < minimum:
        return minimum
    if value > maximum:
        return maximum
    return value


_BLOCK_SIZE = _env_int(
    "ANNOUNCEFLOW_STREAM_BLOCK_SIZE",
    735,   # ~16ms at 44100 Hz (1470 bytes < 1500 MTU, avoids IP fragmentation)
    220,   # ~5ms
    8820,  # ~200ms
)
# Prefer stereo first: some Windows/WASAPI setups produce noise on mono-only open.
_CHANNEL_CANDIDATES = (2, 1)
_TELEMETRY_INTERVAL_SEC = 10.0


def _utc_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _format_exception(exc: Optional[BaseException]) -> Optional[str]:
    """Render exception with traceback when available."""
    if exc is None:
        return None
    tb = exc.__traceback__
    if tb is not None:
        return "".join(traceback.format_exception(exc.__class__, exc, tb, limit=8))
    return f"{exc.__class__.__name__}: {exc}"


def _runtime_reports_dir() -> str:
    base = os.environ.get("ANNOUNCEFLOW_AGENT_RUNTIME_DIR", "").strip()
    if not base:
        base = os.path.join(os.path.expanduser("~"), ".announceflow")
    return os.path.join(base, "logs", "stream_attempts")


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
        self.last_error_details: Optional[str] = None
        self._attempt_seq = 0
        self._attempt: Optional[Dict[str, Any]] = None
        self._last_attempt: Optional[Dict[str, Any]] = None
        self._finalized_attempt_id: Optional[str] = None

    # --------------- Attempt Diagnostics ---------------

    def _new_attempt(
        self, target_host: str, target_port: int, correlation_id: Optional[str] = None
    ) -> str:
        self._attempt_seq += 1
        attempt_id = f"{int(time.time() * 1000)}-{self._attempt_seq}"
        self._attempt = {
            "attempt_id": attempt_id,
            "correlation_id": correlation_id,
            "started_at": _utc_now(),
            "ended_at": None,
            "success": False,
            "stage": "initialized",
            "target_host": target_host,
            "target_port": int(target_port),
            "resolved_host": None,
            "speaker_name": None,
            "capture_device_name": None,
            "sample_rate": _SAMPLE_RATE,
            "channels": None,
            "packet_count": 0,
            "first_packet_at": None,
            "last_packet_at": None,
            "error_code": None,
            "error_type": None,
            "error_message": None,
            "traceback": None,
            "open_errors": [],
            "stages": [],
        }
        self._finalized_attempt_id = None
        self.last_error = None
        self.last_error_details = None
        return attempt_id

    def _resolve_capture_device(self, sc_module: Any, speaker: Any):
        """Resolve a recorder-capable loopback capture device.

        soundcard API differs across versions:
        - Some expose speaker.recorder(...)
        - Others require get_microphone(..., include_loopback=True)
        """
        errors = []

        def _is_loopback_mic(mic: Any, speaker_name: str) -> bool:
            """Best-effort loopback detection to avoid selecting physical microphones."""
            try:
                if bool(getattr(mic, "isloopback", False)):
                    return True
            except Exception:
                pass
            mic_name = str(getattr(mic, "name", "") or "").strip()
            low_name = mic_name.lower()
            if "loopback" in low_name:
                return True
            if speaker_name:
                low_speaker = speaker_name.lower()
                if low_speaker and (low_speaker in low_name or low_name in low_speaker):
                    return True
            return False

        speaker_recorder = getattr(speaker, "recorder", None)
        if callable(speaker_recorder):
            return speaker, errors

        speaker_name = str(getattr(speaker, "name", "") or "")
        speaker_id = getattr(speaker, "id", None)

        get_microphone = getattr(sc_module, "get_microphone", None)
        if callable(get_microphone):
            candidates = []
            if speaker_id not in (None, ""):
                candidates.append(str(speaker_id))
            if speaker_name:
                candidates.append(speaker_name)
            seen = set()
            for ident in candidates:
                if ident in seen:
                    continue
                seen.add(ident)
                try:
                    mic = get_microphone(id=ident, include_loopback=True)
                    if mic is not None and callable(getattr(mic, "recorder", None)):
                        return mic, errors
                    if mic is not None:
                        errors.append(f"loopback microphone '{ident}' has no recorder()")
                except Exception as exc:
                    errors.append(f"get_microphone('{ident}') failed: {exc}")

        all_microphones = getattr(sc_module, "all_microphones", None)
        if callable(all_microphones):
            microphones = []
            try:
                microphones = list(all_microphones(include_loopback=True))
            except TypeError:
                try:
                    microphones = list(all_microphones())
                except Exception as exc:
                    errors.append(f"all_microphones() failed: {exc}")
            except Exception as exc:
                errors.append(f"all_microphones(include_loopback=True) failed: {exc}")

            if microphones:
                # Only accept likely loopback microphones to prevent accidentally opening
                # a physical mic (which can trigger Windows communications ducking/mute).
                loopback_candidates = [
                    mic for mic in microphones if _is_loopback_mic(mic, speaker_name)
                ]
                if loopback_candidates:
                    picked = loopback_candidates[0]
                    if callable(getattr(picked, "recorder", None)):
                        return picked, errors
                    errors.append(
                        (
                            "loopback microphone "
                            f"'{getattr(picked, 'name', 'unknown')}' has no recorder()"
                        )
                    )
                else:
                    errors.append(
                        "No loopback microphone matched default speaker in all_microphones()"
                    )

        errors.append("No WASAPI loopback capture device with recorder() found")
        return None, errors

    def _log_event(self, event: str, **data: Any) -> None:
        attempt_id = None
        correlation_id = None
        if self._attempt:
            attempt_id = self._attempt.get("attempt_id")
            correlation_id = self._attempt.get("correlation_id")
        payload = {
            "ts": _utc_now(),
            "event": event,
            "attempt_id": attempt_id,
            "correlation_id": correlation_id,
        }
        payload.update(data)
        try:
            stream_logger.info(json.dumps(payload, ensure_ascii=False))
        except Exception:
            # Never fail stream flow due to diagnostics logging.
            pass

    def _mark_stage(self, stage: str, **data: Any) -> None:
        if not self._attempt:
            return
        entry = {"ts": _utc_now(), "stage": stage}
        if data:
            entry["data"] = data
        self._attempt["stage"] = stage
        self._attempt["stages"].append(entry)
        self._log_event("stage", stage=stage, **data)

    def _persist_attempt_report(self, snapshot: Dict[str, Any]) -> None:
        try:
            directory = _runtime_reports_dir()
            os.makedirs(directory, exist_ok=True)
            attempt_id = snapshot.get("attempt_id", "unknown")
            path = os.path.join(directory, f"stream_attempt_{attempt_id}.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump(snapshot, f, ensure_ascii=False, indent=2)
            self._log_event("attempt_report_written", path=path)
        except Exception as exc:
            # Do not break streaming, but keep this observable.
            logger.warning("StreamClient: attempt report write failed: %s", exc)
            self._log_event("attempt_report_write_failed", error=str(exc))

    def _finalize_attempt(self, success: bool) -> None:
        if not self._attempt:
            return
        attempt_id = self._attempt.get("attempt_id")
        if self._finalized_attempt_id == attempt_id:
            return
        self._attempt["ended_at"] = _utc_now()
        self._attempt["success"] = bool(success)
        snapshot = dict(self._attempt)
        self._last_attempt = snapshot
        self._finalized_attempt_id = attempt_id
        self._log_event(
            "attempt_finalized",
            success=bool(success),
            error_code=snapshot.get("error_code"),
            packet_count=snapshot.get("packet_count"),
        )
        self._persist_attempt_report(snapshot)

    def _record_failure(
        self,
        error_code: str,
        message: str,
        *,
        exc: Optional[BaseException] = None,
        stage: Optional[str] = None,
    ) -> None:
        self.last_error = error_code
        self.last_error_details = message
        if self._attempt:
            self._attempt["error_code"] = error_code
            self._attempt["error_message"] = message
            self._attempt["error_type"] = (
                exc.__class__.__name__ if exc is not None else None
            )
            self._attempt["traceback"] = _format_exception(exc)
        self._mark_stage(stage or "failed", error_code=error_code)
        self._log_event(
            "failure",
            error_code=error_code,
            message=message,
            error_type=exc.__class__.__name__ if exc is not None else None,
        )

    def get_attempt_snapshot(self) -> Dict[str, Any]:
        """Return current/latest attempt snapshot for diagnostics."""
        with self._lock:
            data = self._attempt if self._attempt is not None else self._last_attempt
            if data is None:
                return {}
            snapshot = dict(data)
            snapshot["running"] = bool(
                self._running
                and self._thread is not None
                and self._thread.is_alive()
            )
            snapshot["last_error"] = self.last_error
            snapshot["last_error_details"] = self.last_error_details
            return snapshot

    def build_failure_report(self) -> str:
        """Return a concise multi-line report for support/triage."""
        snap = self.get_attempt_snapshot()
        if not snap:
            return "No stream attempt snapshot available."
        lines = [
            "=== Stream Attempt Report ===",
            f"attempt_id={snap.get('attempt_id')}",
            f"correlation_id={snap.get('correlation_id')}",
            f"stage={snap.get('stage')}",
            f"success={snap.get('success')}",
            f"running={snap.get('running')}",
            f"target={snap.get('target_host')}:{snap.get('target_port')}",
            f"resolved_host={snap.get('resolved_host')}",
            f"speaker={snap.get('speaker_name')}",
            f"capture_device={snap.get('capture_device_name')}",
            f"sample_rate={snap.get('sample_rate')}",
            f"channels={snap.get('channels')}",
            f"packet_count={snap.get('packet_count')}",
            f"first_packet_at={snap.get('first_packet_at')}",
            f"last_packet_at={snap.get('last_packet_at')}",
            f"error_code={snap.get('error_code')}",
            f"error_type={snap.get('error_type')}",
            f"error_message={snap.get('error_message')}",
            f"open_error_count={len(snap.get('open_errors') or [])}",
        ]
        open_errors = snap.get("open_errors") or []
        if open_errors:
            last_open = open_errors[-1]
            lines.append(
                "last_open_error="
                f"{last_open.get('error_type')}:{last_open.get('error')}"
            )
        return "\n".join(lines)

    def record_external_failure(self, error_code: str, message: str) -> None:
        """Allow caller to attach a failure to current attempt."""
        with self._lock:
            self._record_failure(error_code, message, stage="external_failure")
            self._finalize_attempt(success=False)

    def start_sender(
        self, target_host: str, target_port: int, correlation_id: Optional[str] = None
    ) -> bool:
        """Start capturing system audio and sending to Pi4 via UDP.

        Args:
            target_host: Pi4 IP address or hostname.
            target_port: Port the receiver is listening on.
            correlation_id: Optional session id shared with server logs.

        Returns:
            True if sender started (or was already running), False on error.
        """
        with self._lock:
            if self._running:
                return True

            attempt_id = self._new_attempt(target_host, target_port, correlation_id)
            self._mark_stage("host_resolve_start")

            # Resolve host before starting capture thread.
            # If resolution fails (mDNS/IPv6/corp DNS edge-cases), keep the original
            # host and let UDP send path decide.
            resolved = target_host
            try:
                resolved = socket.gethostbyname(target_host)
                if self._attempt:
                    self._attempt["resolved_host"] = resolved
                self._mark_stage("host_resolved", resolved_host=resolved)
            except Exception as exc:
                if self._attempt:
                    self._attempt["resolved_host"] = target_host
                self._mark_stage(
                    "host_resolve_warning",
                    target_host=target_host,
                    warning=str(exc),
                )
                self._log_event(
                    "host_resolve_warning",
                    target_host=target_host,
                    warning=str(exc),
                )
                logger.warning(
                    "StreamClient: host resolve failed for '%s', continuing with raw host: %s",
                    target_host,
                    exc,
                )

            self._mark_stage("audio_init_start")

            # Verify soundcard can find a speaker for loopback.
            try:
                import soundcard as sc
                speaker = sc.default_speaker()
                if speaker is None:
                    self._record_failure(
                        "no_audio_device",
                        "No default speaker found for WASAPI loopback",
                        stage="audio_init_failed",
                    )
                    self._finalize_attempt(success=False)
                    return False
                capture_device, resolve_errors = self._resolve_capture_device(sc, speaker)
                if capture_device is None:
                    if self._attempt is not None:
                        for err in resolve_errors[-6:]:
                            self._attempt["open_errors"].append(
                                {
                                    "ts": _utc_now(),
                                    "channels": None,
                                    "error_type": "CaptureDeviceResolveError",
                                    "error": err,
                                }
                            )
                    self._record_failure(
                        "recorder_open_failed",
                        "No compatible WASAPI loopback recorder found",
                        stage="audio_init_failed",
                    )
                    self._finalize_attempt(success=False)
                    return False
                if self._attempt:
                    self._attempt["speaker_name"] = speaker.name
                    self._attempt["capture_device_name"] = getattr(
                        capture_device, "name", None
                    )
                self._mark_stage(
                    "audio_init_ok",
                    speaker=speaker.name,
                    capture_device=getattr(capture_device, "name", None),
                )
                logger.info(
                    (
                        "StreamClient: will capture from speaker '%s' via loopback "
                        "device '%s' (attempt_id=%s)"
                    ),
                    speaker.name,
                    getattr(capture_device, "name", "unknown"),
                    attempt_id,
                )
            except Exception as exc:
                self._record_failure(
                    "no_audio_device",
                    "soundcard initialization failed",
                    exc=exc,
                    stage="audio_init_failed",
                )
                self._finalize_attempt(success=False)
                return False

            self._running = True
            self._mark_stage("capture_thread_start")
            self._thread = threading.Thread(
                target=self._capture_loop,
                args=(resolved, target_port),
                daemon=True,
            )
            self._thread.start()

            # Brief health check: let thread start and catch immediate errors
            self._thread.join(timeout=0.35)
            if not self._running:
                # Thread set _running to False → startup failed
                if self.last_error is None:
                    self._record_failure(
                        "capture_thread_died",
                        "Capture thread died during startup health check",
                        stage="capture_thread_died",
                    )
                self._finalize_attempt(success=False)
                logger.error(
                    "StreamClient: capture thread died on startup (attempt_id=%s)",
                    attempt_id,
                )
                self._thread = None
                return False

            self._mark_stage("steady_capture")
            logger.info(
                "StreamClient: sender started (target=%s:%d, attempt_id=%s)",
                resolved,
                target_port,
                attempt_id,
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
            self._mark_stage("stop_requested")
            self._running = False

        # Wait outside lock so capture loop can finish
        if self._thread is not None:
            self._thread.join(timeout=3)
            self._thread = None

        with self._lock:
            if self.last_error is None:
                self._mark_stage("stopped")
                self._finalize_attempt(success=True)
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
        sent_any_packet = False
        try:
            import numpy as np
            import soundcard as sc

            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            speaker = sc.default_speaker()
            if speaker is None:
                raise RuntimeError("No default speaker available in capture loop")
            capture_device, resolve_errors = self._resolve_capture_device(sc, speaker)
            if capture_device is None:
                if self._attempt is not None:
                    for err in resolve_errors[-6:]:
                        self._attempt["open_errors"].append(
                            {
                                "ts": _utc_now(),
                                "channels": None,
                                "error_type": "CaptureDeviceResolveError",
                                "error": err,
                            }
                        )
                self._record_failure(
                    "recorder_open_failed",
                    "No compatible WASAPI loopback recorder found",
                    stage="recorder_open_failed",
                )
                return

            last_open_exc: Optional[BaseException] = None
            for channels in _CHANNEL_CANDIDATES:
                if not self._running:
                    break
                try:
                    self._mark_stage("recorder_open_start", channels=channels)
                    with capture_device.recorder(
                        samplerate=_SAMPLE_RATE,
                        channels=channels,
                        blocksize=_BLOCK_SIZE,
                    ) as recorder:
                        if self._attempt:
                            self._attempt["channels"] = channels
                            self._attempt["speaker_name"] = speaker.name
                            self._attempt["capture_device_name"] = getattr(
                                capture_device, "name", None
                            )
                        self._mark_stage("recorder_open_ok", channels=channels)
                        _pkt_bytes = _BLOCK_SIZE * 1 * 2  # mono * 16-bit
                        logger.info(
                            "StreamClient: capture started "
                            "speaker=%s capture_device=%s "
                            "rate=%d channels=%d(capture)->1(mono) "
                            "block=%d frames (%dms, %d bytes/pkt, %s MTU) "
                            "target=%s:%d",
                            getattr(speaker, "name", "?"),
                            getattr(capture_device, "name", "?"),
                            _SAMPLE_RATE,
                            channels,
                            _BLOCK_SIZE,
                            int(_BLOCK_SIZE * 1000 / _SAMPLE_RATE),
                            _pkt_bytes,
                            "under" if _pkt_bytes <= 1472 else "OVER",
                            host,
                            port,
                        )
                        last_telemetry_mono = time.monotonic()
                        last_telemetry_packets = 0
                        while self._running:
                            # soundcard returns float32 in [-1.0, 1.0]
                            data = recorder.record(numframes=_BLOCK_SIZE)
                            clean = np.nan_to_num(
                                data, nan=0.0, posinf=1.0, neginf=-1.0
                            )
                            if getattr(clean, "ndim", 1) > 1:
                                # Downmix stereo/multi-channel to mono.
                                clean = clean.mean(axis=1)
                            pcm = np.clip(clean * 32767, -32768, 32767).astype(
                                np.dtype("<i2")
                            )
                            try:
                                sock.sendto(pcm.tobytes(), (host, port))
                            except OSError as send_exc:
                                self._record_failure(
                                    "udp_send_failed",
                                    "Failed to send UDP audio packet",
                                    exc=send_exc,
                                    stage="udp_send_failed",
                                )
                                return

                            now = _utc_now()
                            sent_any_packet = True
                            if self._attempt:
                                self._attempt["packet_count"] += 1
                                if self._attempt["first_packet_at"] is None:
                                    self._attempt["first_packet_at"] = now
                                    self._mark_stage("first_packet_send")
                                self._attempt["last_packet_at"] = now
                                if self._attempt["packet_count"] == 25:
                                    self._mark_stage("steady_capture")
                                now_mono = time.monotonic()
                                elapsed = now_mono - last_telemetry_mono
                                if elapsed >= _TELEMETRY_INTERVAL_SEC:
                                    total_packets = int(self._attempt["packet_count"])
                                    delta_packets = max(
                                        0, total_packets - last_telemetry_packets
                                    )
                                    packets_per_sec = (
                                        round(delta_packets / elapsed, 2)
                                        if elapsed > 0
                                        else 0.0
                                    )
                                    self._log_event(
                                        "capture_telemetry",
                                        packet_count=total_packets,
                                        delta_packets=delta_packets,
                                        interval_seconds=round(elapsed, 3),
                                        packets_per_sec=packets_per_sec,
                                        channels=channels,
                                        sample_rate=_SAMPLE_RATE,
                                        block_size=_BLOCK_SIZE,
                                    )
                                    last_telemetry_mono = now_mono
                                    last_telemetry_packets = total_packets
                    # Recorder block exited gracefully.
                    break
                except Exception as open_exc:
                    last_open_exc = open_exc
                    if self._attempt is not None:
                        self._attempt["open_errors"].append(
                            {
                                "ts": _utc_now(),
                                "channels": channels,
                                "error_type": open_exc.__class__.__name__,
                                "error": str(open_exc),
                            }
                        )
                    logger.warning(
                        "StreamClient: recorder open failed (channels=%s): %s",
                        channels,
                        open_exc,
                    )
                    self._log_event(
                        "recorder_open_retry",
                        channels=channels,
                        error=str(open_exc),
                    )
                    continue

            if not sent_any_packet and last_open_exc is not None:
                self._record_failure(
                    "recorder_open_failed",
                    "Failed to open loopback recorder for all channel candidates",
                    exc=last_open_exc,
                    stage="recorder_open_failed",
                )
                return

        except Exception as exc:
            self._record_failure(
                "capture_error",
                "Unexpected capture loop error",
                exc=exc,
                stage="capture_error",
            )
        finally:
            if sock is not None:
                sock.close()
            self._running = False
            with self._lock:
                success = self.last_error is None and sent_any_packet
                if not success and self.last_error is None:
                    self._record_failure(
                        "capture_thread_died",
                        "Capture loop ended before first packet",
                        stage="capture_thread_died",
                    )
                self._finalize_attempt(success=success)
            logger.info("StreamClient: capture loop ended")
