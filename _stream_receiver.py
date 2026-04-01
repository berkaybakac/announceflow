"""
AnnounceFlow - Stream Receiver (V1)

Receives raw PCM audio over UDP and plays through ALSA via ffmpeg.
This script is spawned by StreamManager as a subprocess.

Audio format: s16le, 44100 Hz, mono
Transport: UDP on configurable port (default 5800)

Usage: python _stream_receiver.py [port] [alsa_device]
"""
import atexit
import json
import os
import re
import shlex
import signal
import subprocess
import sys
import tempfile
import threading
import time
from collections import deque
from datetime import datetime, timezone
from typing import Any, Dict, Optional


DEFAULT_FFMPEG_LOG_MAX_BYTES = 2_000_000
DEFAULT_FFMPEG_LOG_BACKUP_COUNT = 5


def _parse_positive_int_env(var_name: str, default: int) -> int:
    raw = os.environ.get(var_name, "").strip()
    if not raw:
        return int(default)
    try:
        parsed = int(raw)
    except (TypeError, ValueError):
        return int(default)
    return max(1, parsed)


class _RotatingLineFile:
    """Thread-safe append writer with bounded on-disk size."""

    def __init__(self, path: str, *, max_bytes: int, backup_count: int):
        self.path = path
        self.max_bytes = max(1, int(max_bytes))
        self.backup_count = max(1, int(backup_count))
        self._lock = threading.Lock()
        self._stream = None
        self._open_stream()

    def _open_stream(self) -> None:
        self._stream = open(
            self.path,
            "a",
            encoding="utf-8",
            errors="replace",
            buffering=1,
        )

    def _rotate_locked(self) -> None:
        if self._stream and not self._stream.closed:
            self._stream.close()

        oldest = f"{self.path}.{self.backup_count}"
        if os.path.exists(oldest):
            try:
                os.remove(oldest)
            except OSError:
                pass

        for idx in range(self.backup_count - 1, 0, -1):
            src = f"{self.path}.{idx}"
            dst = f"{self.path}.{idx + 1}"
            if os.path.exists(src):
                try:
                    os.replace(src, dst)
                except OSError:
                    continue

        if os.path.exists(self.path):
            try:
                os.replace(self.path, f"{self.path}.1")
            except OSError:
                pass

        self._open_stream()

    def write(self, text: str) -> None:
        payload = text or ""
        payload_size = len(payload.encode("utf-8", errors="replace"))
        with self._lock:
            try:
                current_size = (
                    os.path.getsize(self.path) if os.path.exists(self.path) else 0
                )
            except OSError:
                current_size = 0

            if (current_size + payload_size) > self.max_bytes:
                self._rotate_locked()

            self._stream.write(payload)

    def flush(self) -> None:
        with self._lock:
            if self._stream and not self._stream.closed:
                self._stream.flush()

    def close(self) -> None:
        with self._lock:
            if self._stream and not self._stream.closed:
                self._stream.close()


def _utc_iso_ms() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace(
        "+00:00", "Z"
    )


def _local_log_ts() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]


def _safe_log_system(event: str, data: dict) -> None:
    try:
        from logger import log_system

        log_system(event, data)
    except Exception:
        pass


def _safe_log_error(event: str, data: dict) -> None:
    try:
        from logger import log_error

        log_error(event, data)
    except Exception:
        pass


def _read_proc_stat_snapshot() -> dict:
    """Read CPU and memory from /proc on Linux. Returns fallbacks on other OS."""
    snapshot: Dict[str, Any] = {"cpu_pct": -1.0, "mem_available_mb": -1}
    try:
        with open("/proc/loadavg") as f:
            snapshot["load_1m"] = float(f.read().split()[0])
    except Exception:
        snapshot["load_1m"] = -1.0
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemAvailable:"):
                    snapshot["mem_available_mb"] = int(line.split()[1]) // 1024
                    break
    except Exception:
        pass
    return snapshot


# Throttle xrun snapshots: at most one per 30 seconds to avoid log flood.
_last_xrun_snapshot_mono: float = 0.0
_XRUN_SNAPSHOT_INTERVAL: float = 30.0


# Throttle jitter anomaly logs: at most one per 60 seconds.
_last_jitter_anomaly_mono: float = 0.0
_JITTER_ANOMALY_INTERVAL: float = 60.0


# Xrun status file: receiver writes current count so StreamManager can read it.
_XRUN_STATUS_DIR = os.environ.get("ANNOUNCEFLOW_LOG_DIR", "").strip() or os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "logs"
)
XRUN_STATUS_FILE = os.path.join(_XRUN_STATUS_DIR, "receiver_xrun_status.json")
_last_xrun_status_write_mono: float = 0.0
_XRUN_STATUS_WRITE_INTERVAL: float = 1.0


def _parse_utc_ts(raw: Any) -> Optional[datetime]:
    text = str(raw or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _ensure_xrun_telemetry_state(counters: Dict[str, Any]) -> None:
    if not isinstance(counters.get("xrun_events_last_1s"), deque):
        counters["xrun_events_last_1s"] = deque()
    if not isinstance(counters.get("xrun_events_last_60s"), deque):
        counters["xrun_events_last_60s"] = deque()
    counters["xrun_peak_1s"] = int(counters.get("xrun_peak_1s") or 0)
    counters["xrun_peak_60s"] = int(counters.get("xrun_peak_60s") or 0)
    counters["xrun_current_consecutive"] = int(counters.get("xrun_current_consecutive") or 0)
    counters["xrun_max_consecutive"] = int(counters.get("xrun_max_consecutive") or 0)
    counters["xrun_underrun_count"] = int(counters.get("xrun_underrun_count") or 0)
    counters["xrun_overrun_count"] = int(counters.get("xrun_overrun_count") or 0)
    counters["xrun_unknown_count"] = int(counters.get("xrun_unknown_count") or 0)
    counters["last_xrun_type"] = _normalize_xrun_type(counters.get("last_xrun_type"))
    last_type_source = str(counters.get("last_xrun_type_source") or "").strip()
    counters["last_xrun_type_source"] = last_type_source or "unknown"


def _normalize_xrun_type(raw: Any) -> str:
    text = str(raw or "").strip().lower()
    if text in {"underrun", "overrun"}:
        return text
    return "unknown"


def _classify_alsa_xrun_type(line: str) -> tuple[str, str]:
    lower = (line or "").lower()
    if "underrun" in lower:
        return "underrun", "ffmpeg_log"
    if "overrun" in lower:
        return "overrun", "ffmpeg_log"
    # This receiver only uses ALSA as an output sink, so generic xrun means
    # playback starvation (underrun) unless ffmpeg explicitly says otherwise.
    return "underrun", "inferred_playback_pipeline"


def _record_xrun_type_hits(counters: Dict[str, Any], xrun_type: str, hit_count: int) -> None:
    normalized = _normalize_xrun_type(xrun_type)
    key = "xrun_unknown_count"
    if normalized == "underrun":
        key = "xrun_underrun_count"
    elif normalized == "overrun":
        key = "xrun_overrun_count"
    counters[key] = int(counters.get(key) or 0) + int(hit_count)


def _record_xrun_hits(
    counters: Dict[str, Any],
    hit_count: int,
    event_ts: str,
    *,
    xrun_type: str = "unknown",
    xrun_type_source: str = "unknown",
) -> None:
    if hit_count <= 0:
        return
    _ensure_xrun_telemetry_state(counters)
    counters["alsa_xrun"] = int(counters.get("alsa_xrun") or 0) + int(hit_count)
    normalized_type = _normalize_xrun_type(xrun_type)
    _record_xrun_type_hits(counters, normalized_type, hit_count)
    counters["last_xrun_type"] = normalized_type
    source = str(xrun_type_source or "").strip()
    counters["last_xrun_type_source"] = source or "unknown"
    counters["last_xrun_at"] = event_ts
    if counters.get("first_xrun_at") is None:
        counters["first_xrun_at"] = event_ts
    counters["repeat_context"] = "alsa_xrun"

    now_mono = time.monotonic()
    hits_1s = counters["xrun_events_last_1s"]
    hits_60s = counters["xrun_events_last_60s"]
    if hit_count == 1:
        hits_1s.append(now_mono)
        hits_60s.append(now_mono)
    else:
        stamp = [now_mono] * hit_count
        hits_1s.extend(stamp)
        hits_60s.extend(stamp)

    cutoff_1s = now_mono - 1.0
    while hits_1s and hits_1s[0] < cutoff_1s:
        hits_1s.popleft()
    cutoff_60s = now_mono - 60.0
    while hits_60s and hits_60s[0] < cutoff_60s:
        hits_60s.popleft()

    current_1s = len(hits_1s)
    current_60s = len(hits_60s)
    counters["xrun_peak_1s"] = max(int(counters.get("xrun_peak_1s") or 0), current_1s)
    counters["xrun_peak_60s"] = max(int(counters.get("xrun_peak_60s") or 0), current_60s)

    consecutive = int(counters.get("xrun_current_consecutive") or 0) + int(hit_count)
    counters["xrun_current_consecutive"] = consecutive
    counters["xrun_max_consecutive"] = max(
        int(counters.get("xrun_max_consecutive") or 0),
        consecutive,
    )


def _calc_xrun_burst_rate_per_sec(counters: Dict[str, Any]) -> float:
    xrun_count = int(counters.get("alsa_xrun") or 0)
    if xrun_count <= 0:
        return 0.0
    first_xrun_at = _parse_utc_ts(counters.get("first_xrun_at"))
    last_xrun_at = _parse_utc_ts(counters.get("last_xrun_at"))
    if first_xrun_at and last_xrun_at:
        burst_seconds = (last_xrun_at - first_xrun_at).total_seconds()
        if burst_seconds > 0:
            return round(xrun_count / burst_seconds, 3)
    return float(xrun_count)


def _calc_stream_quality_summary(
    counters: Dict[str, Any],
    *,
    duration_seconds: float,
) -> Dict[str, Any]:
    """Build a simple quality score from xrun/overrun rates.

    Formula v1:
    - xrun penalty: 10 points per xrun/minute
    - udp overrun penalty: 15 points per overrun/minute
    - quality_pct: clamp(100 - penalties, 0..100)
    """
    duration = max(0.0, float(duration_seconds or 0.0))
    duration_minutes = duration / 60.0 if duration > 0 else 0.0
    # Normalize by at least one minute to avoid pathological penalties
    # for short startup/teardown sessions.
    normalized_minutes = max(1.0, duration_minutes)

    alsa_xrun_total = int(counters.get("alsa_xrun") or 0)
    udp_overrun_total = int(counters.get("udp_overrun") or 0)
    xrun_rate_per_min = round(alsa_xrun_total / normalized_minutes, 3)
    udp_overrun_rate_per_min = round(udp_overrun_total / normalized_minutes, 3)

    xrun_penalty = xrun_rate_per_min * 10.0
    udp_overrun_penalty = udp_overrun_rate_per_min * 15.0
    quality_pct = round(
        max(0.0, min(100.0, 100.0 - xrun_penalty - udp_overrun_penalty)),
        2,
    )

    return {
        "duration_seconds": round(duration, 3),
        "duration_minutes": round(duration_minutes, 3),
        "alsa_xrun_total": alsa_xrun_total,
        "udp_overrun_total": udp_overrun_total,
        "xrun_rate_per_min": xrun_rate_per_min,
        "udp_overrun_rate_per_min": udp_overrun_rate_per_min,
        "quality_pct": quality_pct,
        "quality_formula_version": "v1_rate_penalty",
    }


def _write_xrun_status(
    counters: Dict[str, Any],
    correlation_id: str,
    *,
    force: bool = False,
) -> None:
    """Atomically write xrun count to a status file for StreamManager to read."""
    global _last_xrun_status_write_mono
    now_mono = time.monotonic()
    if not force and (now_mono - _last_xrun_status_write_mono) < _XRUN_STATUS_WRITE_INTERVAL:
        return
    _last_xrun_status_write_mono = now_mono

    _ensure_xrun_telemetry_state(counters)
    try:
        os.makedirs(_XRUN_STATUS_DIR, exist_ok=True)
        elapsed = 0.0
        started_mono = float(counters.get("started_mono") or 0.0)
        if started_mono > 0:
            elapsed = max(0.0, now_mono - started_mono)
        session_rate = (
            round((int(counters.get("alsa_xrun") or 0) / elapsed), 3)
            if elapsed > 0
            else 0.0
        )
        data = {
            "alsa_xrun": counters.get("alsa_xrun", 0),
            "udp_overrun": counters.get("udp_overrun", 0),
            "mono_ts": time.monotonic(),
            "correlation_id": correlation_id,
            "xrun_peak_1s": counters.get("xrun_peak_1s", 0),
            "xrun_peak_60s": counters.get("xrun_peak_60s", 0),
            "xrun_max_consecutive": counters.get("xrun_max_consecutive", 0),
            "xrun_current_consecutive": counters.get("xrun_current_consecutive", 0),
            "xrun_session_rate_per_sec": session_rate,
            "xrun_burst_rate_per_sec": _calc_xrun_burst_rate_per_sec(counters),
            "xrun_underrun_count": counters.get("xrun_underrun_count", 0),
            "xrun_overrun_count": counters.get("xrun_overrun_count", 0),
            "xrun_unknown_count": counters.get("xrun_unknown_count", 0),
            "last_xrun_type": counters.get("last_xrun_type", "unknown"),
            "last_xrun_type_source": counters.get("last_xrun_type_source", "unknown"),
        }
        tmp_fd, tmp_path = tempfile.mkstemp(
            dir=_XRUN_STATUS_DIR, prefix=".xrun_status_", suffix=".tmp"
        )
        try:
            with os.fdopen(tmp_fd, "w") as f:
                json.dump(data, f)
            os.replace(tmp_path, XRUN_STATUS_FILE)
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
    except Exception:
        pass


def _log_jitter_anomaly(
    counters: Dict[str, Any], correlation_id: str, trigger: str
) -> None:
    """Log network-level anomaly when UDP overrun or rapid xrun burst occurs.

    UDP circular buffer overrun means the sender is pushing data faster
    than ffmpeg can consume, OR packets arrived in a burst after a gap
    (network jitter).  Logging this alongside xrun helps isolate whether
    the root cause is sender-side (CPU spike), network (WiFi jitter), or
    Pi-side (ALSA stall).  Throttled to one per 60 s.
    """
    global _last_jitter_anomaly_mono
    now = time.monotonic()
    if now - _last_jitter_anomaly_mono < _JITTER_ANOMALY_INTERVAL:
        return
    _last_jitter_anomaly_mono = now

    snap = _read_proc_stat_snapshot()
    _safe_log_error(
        "stream_jitter_anomaly",
        {
            "correlation_id": correlation_id,
            "trigger": trigger,
            "udp_overrun_total": counters.get("udp_overrun", 0),
            "alsa_xrun_total": counters.get("alsa_xrun", 0),
            "load_1m": snap.get("load_1m", -1.0),
            "mem_available_mb": snap.get("mem_available_mb", -1),
        },
    )


def _log_xrun_snapshot(counters: Dict[str, Any], correlation_id: str) -> None:
    """Log a system-state snapshot at the moment of an ALSA xrun.

    Captures CPU load and available RAM so post-mortem analysis can
    distinguish Pi overload (high CPU) from network starvation (low CPU
    + jitter).  Throttled to one snapshot per 30 s.
    """
    global _last_xrun_snapshot_mono
    now = time.monotonic()
    if now - _last_xrun_snapshot_mono < _XRUN_SNAPSHOT_INTERVAL:
        return
    _last_xrun_snapshot_mono = now

    snap = _read_proc_stat_snapshot()
    snap["correlation_id"] = correlation_id
    snap["xrun_count_so_far"] = counters.get("alsa_xrun", 0)
    snap["udp_overrun_so_far"] = counters.get("udp_overrun", 0)
    snap["xrun_underrun_so_far"] = counters.get("xrun_underrun_count", 0)
    snap["xrun_overrun_so_far"] = counters.get("xrun_overrun_count", 0)
    snap["xrun_unknown_so_far"] = counters.get("xrun_unknown_count", 0)
    snap["last_xrun_type"] = counters.get("last_xrun_type", "unknown")
    snap["last_xrun_type_source"] = counters.get("last_xrun_type_source", "unknown")
    _safe_log_error("xrun_snapshot", snap)
    _write_xrun_status(counters, correlation_id)


def _find_ffmpeg():
    """Return path to ffmpeg binary."""
    if getattr(sys, "frozen", False):
        bundled = os.path.join(sys._MEIPASS, "ffmpeg")
        if os.path.isfile(bundled):
            return bundled
    return "ffmpeg"


def _resolve_alsa_device():
    """Resolve ALSA output device using same logic as player.py.

    Priority: CLI arg > ANNOUNCEFLOW_ALSA_DEVICE env > probe candidates.
    """

    def _probe_candidate(candidate: str) -> bool:
        """Return True when ALSA device accepts a short raw-silence playback."""
        try:
            probe = subprocess.run(
                [
                    "aplay",
                    "-q",
                    "-D",
                    candidate,
                    "-t",
                    "raw",
                    "-f",
                    "S16_LE",
                    "-r",
                    "44100",
                    "-c",
                    "1",
                    "-d",
                    "1",
                    "/dev/zero",
                ],
                capture_output=True,
                timeout=4,
            )
            return probe.returncode == 0
        except (OSError, subprocess.TimeoutExpired):
            return False

    def _detect_preferred_card_candidates():
        """Derive good ALSA card candidates from `aplay -l` output."""
        try:
            probe = subprocess.run(
                ["aplay", "-l"],
                capture_output=True,
                text=True,
                timeout=3,
            )
        except (OSError, subprocess.TimeoutExpired):
            return []

        if probe.returncode != 0:
            return []

        cards = []
        for line in (probe.stdout or "").splitlines():
            m = re.search(
                r"card\s+(\d+)\s*:\s*([^\[]+)\[([^\]]+)\]", line, re.IGNORECASE
            )
            if not m:
                continue
            card_idx = m.group(1)
            card_id = (m.group(2) or "").strip()
            card_desc = (m.group(3) or "").strip()
            cards.append((card_idx, card_id, card_desc))

        if not cards:
            return []

        candidates = []
        # Prefer analog 3.5mm headphones card when present.
        for idx, card_id, card_desc in cards:
            low = f"{card_id} {card_desc}".lower()
            if "headphone" in low:
                candidates.append(f"plughw:{idx},0")
                break
        # Then include all discovered cards in order.
        for idx, _, _ in cards:
            candidates.append(f"plughw:{idx},0")
        return candidates

    def _uniq(items):
        seen = set()
        result = []
        for item in items:
            if not item or item in seen:
                continue
            seen.add(item)
            result.append(item)
        return result

    # CLI argument (passed by StreamManager)
    if len(sys.argv) > 2:
        return sys.argv[2]

    # Environment variable
    env_device = os.environ.get("ANNOUNCEFLOW_ALSA_DEVICE", "").strip()
    if env_device:
        return env_device

    env_card = os.environ.get("ANNOUNCEFLOW_ALSA_CARD", "").strip()
    if env_card:
        if env_card.startswith(("plughw:", "hw:")):
            if env_card.startswith("hw:"):
                card_part = env_card.split(":", 1)[1]
                return f"plughw:{card_part}"
            return env_card
        card_part = env_card if "," in env_card else f"{env_card},0"
        return f"plughw:{card_part}"

    # Probe candidates (prefer detected concrete cards; keep default last).
    candidates = _uniq(
        _detect_preferred_card_candidates() + ["plughw:2,0", "plughw:0,0", "default"]
    )
    first_non_default = next((c for c in candidates if c != "default"), None)

    for candidate in candidates:
        if _probe_candidate(candidate):
            return candidate

    # Avoid default when probing failed for all: default was observed to fail with
    # "cannot open audio device default (Unknown error 524)" on Pi deployments.
    if first_non_default:
        return first_non_default
    return "default"


def _build_udp_input_url(port: int) -> str:
    params = ["overrun_nonfatal=1"]

    udp_fifo = os.environ.get("ANNOUNCEFLOW_STREAM_UDP_FIFO", "").strip()
    if udp_fifo.isdigit() and int(udp_fifo) > 0:
        params.append(f"fifo_size={udp_fifo}")
    else:
        # 4 MB default (~47 s at 88.2 kB/s mono 16-bit 44.1 kHz) absorbs
        # network jitter bursts without packet loss, reducing ALSA xruns.
        params.append("fifo_size=4194304")

    return f"udp://0.0.0.0:{port}?{'&'.join(params)}"


def _parse_extra_ffmpeg_args() -> list[str]:
    raw = os.environ.get("ANNOUNCEFLOW_STREAM_FFMPEG_ARGS", "").strip()
    if not raw:
        return []
    try:
        return shlex.split(raw)
    except ValueError:
        return []


def _resolve_ffmpeg_log_rotation() -> tuple[int, int]:
    max_bytes = _parse_positive_int_env(
        "ANNOUNCEFLOW_STREAM_FFMPEG_LOG_MAX_BYTES",
        DEFAULT_FFMPEG_LOG_MAX_BYTES,
    )
    backup_count = _parse_positive_int_env(
        "ANNOUNCEFLOW_STREAM_FFMPEG_LOG_BACKUP_COUNT",
        DEFAULT_FFMPEG_LOG_BACKUP_COUNT,
    )
    return max_bytes, backup_count


def _process_ffmpeg_line(
    line: str,
    log_file,
    counters: Dict[str, Any],
    *,
    correlation_id: str = "",
    port: Optional[int] = None,
    alsa_device: str = "",
) -> None:
    text = (line or "").strip()
    if not text:
        return

    _ensure_xrun_telemetry_state(counters)
    event_ts = _utc_iso_ms()
    log_file.write(f"{_local_log_ts()} {text}\n")
    log_file.flush()

    lower = text.lower()
    line_has_xrun = False
    repeat_match = re.search(r"last message repeated\s+(\d+)\s+times", lower)
    if repeat_match:
        repeated_count = int(repeat_match.group(1))
        repeat_context = counters.get("repeat_context")
        if repeat_context == "udp_overrun":
            counters["udp_overrun"] += repeated_count
            counters["last_overrun_at"] = event_ts
            counters["xrun_current_consecutive"] = 0
        elif repeat_context == "alsa_xrun":
            repeated_xrun_type = counters.get("last_xrun_type", "unknown")
            _record_xrun_hits(
                counters,
                repeated_count,
                event_ts,
                xrun_type=str(repeated_xrun_type),
                xrun_type_source="repeat_context",
            )
            line_has_xrun = True
            _log_xrun_snapshot(counters, correlation_id)
        else:
            counters["xrun_current_consecutive"] = 0
        if correlation_id:
            _write_xrun_status(counters, correlation_id)
        return

    counters["repeat_context"] = None

    if counters.get("first_input_at") is None and "input #0" in lower:
        counters["first_input_at"] = event_ts
        if correlation_id:
            _safe_log_system(
                "stream_receiver_first_input",
                {
                    "correlation_id": correlation_id,
                    "port": port,
                    "alsa_device": alsa_device,
                    "at": event_ts,
                },
            )
    if counters.get("first_output_at") is None and "output #0" in lower:
        counters["first_output_at"] = event_ts
        if correlation_id:
            _safe_log_system(
                "stream_receiver_first_output",
                {
                    "correlation_id": correlation_id,
                    "port": port,
                    "alsa_device": alsa_device,
                    "at": event_ts,
                },
            )

    if "circular buffer overrun" in lower:
        counters["udp_overrun"] += 1
        counters["last_overrun_at"] = event_ts
        if counters.get("first_overrun_at") is None:
            counters["first_overrun_at"] = event_ts
        counters["repeat_context"] = "udp_overrun"
        counters["xrun_current_consecutive"] = 0
        _log_jitter_anomaly(counters, correlation_id, "udp_overrun")
    if "alsa buffer xrun" in lower:
        xrun_type, xrun_type_source = _classify_alsa_xrun_type(text)
        _record_xrun_hits(
            counters,
            1,
            event_ts,
            xrun_type=xrun_type,
            xrun_type_source=xrun_type_source,
        )
        line_has_xrun = True
        _log_xrun_snapshot(counters, correlation_id)
    if "error during demuxing" in lower:
        counters["demux_errors"] += 1
    if "immediate exit requested" in lower:
        counters["immediate_exit"] += 1
    if "cannot open audio device" in lower or "device or resource busy" in lower:
        counters["audio_device_errors"] += 1
    if "connection refused" in lower or "timed out" in lower or "network is unreachable" in lower:
        counters["connection_errors"] += 1
    if not line_has_xrun:
        counters["xrun_current_consecutive"] = 0
    if correlation_id:
        _write_xrun_status(counters, correlation_id)


def _drain_ffmpeg_stderr(
    pipe,
    log_file,
    counters: Dict[str, Any],
    *,
    correlation_id: str = "",
    port: Optional[int] = None,
    alsa_device: str = "",
) -> None:
    if pipe is None:
        return

    try:
        for chunk in iter(pipe.readline, ""):
            if not chunk:
                break
            normalized = chunk.replace("\r", "\n")
            for line in normalized.splitlines():
                _process_ffmpeg_line(
                    line,
                    log_file,
                    counters,
                    correlation_id=correlation_id,
                    port=port,
                    alsa_device=alsa_device,
                )
    except Exception as exc:
        log_file.write(f"{_local_log_ts()} [receiver] stderr_drain_error={exc}\n")
        log_file.flush()


def _resolve_correlation_id() -> str:
    from_env = os.environ.get("ANNOUNCEFLOW_STREAM_CORRELATION_ID", "").strip()
    if from_env:
        return from_env
    return f"local-{os.getpid()}-{int(time.time() * 1000)}"


def stop_process(proc: subprocess.Popen) -> None:
    """Gracefully stop ffmpeg via stdin 'q' + SIGTERM.

    Sends 'q' first (best-effort), then SIGTERM.  FFmpeg with UDP
    input blocks on recvfrom() and never reads stdin, so 'q' alone
    is not reliable.  SIGTERM is a kernel-level signal that reaches
    ffmpeg regardless of what syscall it's blocked on.
    """
    try:
        if proc.stdin and not proc.stdin.closed:
            proc.stdin.write("q")
            proc.stdin.flush()
            proc.stdin.close()
    except (OSError, BrokenPipeError, ValueError):
        pass
    try:
        proc.terminate()
        proc.wait(timeout=1.0)
    except (OSError, ProcessLookupError, subprocess.TimeoutExpired):
        try:
            proc.kill()
        except Exception:
            pass


def _classify_receiver_exit(
    return_code: Optional[int], shutdown_signal_name: Optional[str]
) -> str:
    """Classify receiver exit for telemetry.

    Returns:
        success: Expected clean exit (None/0)
        controlled: Non-zero exit after explicit receiver signal handling
        unexpected: Non-zero exit without controlled shutdown context
    """
    if return_code in (None, 0):
        return "success"
    if shutdown_signal_name:
        return "controlled"
    return "unexpected"

def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 5800
    ffmpeg_bin = _find_ffmpeg()
    alsa_device = _resolve_alsa_device()
    correlation_id = _resolve_correlation_id()

    udp_input_url = _build_udp_input_url(port)
    cmd = [
        ffmpeg_bin,
        "-hide_banner",
        "-nostats",
        "-y",
        # Larger real-time demuxer buffer reduces DTS discontinuity warnings
        # that can cascade into ALSA underruns on network-jittery links.
        "-rtbufsize", "10M",
        "-probesize",
        "32",
        "-analyzeduration",
        "0",
        "-f",
        "s16le",
        "-ar",
        "44100",
        "-ac",
        "1",
        "-i",
        udp_input_url,
        # Smooth timing drift between UDP input clock and ALSA hardware clock.
        # async=1000 tolerates up to ~22 ms of drift via silent sample
        # insert/drop before a hard resync — inaudible for background music,
        # prevents the xrun cascade observed in production (2026-03-18).
        "-af", "aresample=async=1000",
    ]
    cmd.extend(_parse_extra_ffmpeg_args())
    cmd.extend(["-f", "alsa", alsa_device])

    # Log ffmpeg stderr with per-line timestamps for deterministic debugging.
    log_dir = os.environ.get("ANNOUNCEFLOW_LOG_DIR", "").strip()
    if not log_dir:
        log_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
    os.makedirs(log_dir, exist_ok=True)

    log_path = os.path.join(log_dir, "stream_receiver_ffmpeg.log")
    log_max_bytes, log_backup_count = _resolve_ffmpeg_log_rotation()
    stderr_log = _RotatingLineFile(
        log_path,
        max_bytes=log_max_bytes,
        backup_count=log_backup_count,
    )
    started_mono = time.monotonic()

    counters: Dict[str, Any] = {
        "udp_overrun": 0,
        "alsa_xrun": 0,
        "demux_errors": 0,
        "immediate_exit": 0,
        "audio_device_errors": 0,
        "connection_errors": 0,
        "first_input_at": None,
        "first_output_at": None,
        "first_overrun_at": None,
        "last_overrun_at": None,
        "first_xrun_at": None,
        "last_xrun_at": None,
        "xrun_peak_1s": 0,
        "xrun_peak_60s": 0,
        "xrun_current_consecutive": 0,
        "xrun_max_consecutive": 0,
        "xrun_underrun_count": 0,
        "xrun_overrun_count": 0,
        "xrun_unknown_count": 0,
        "last_xrun_type": "unknown",
        "last_xrun_type_source": "unknown",
        "xrun_events_last_1s": deque(),
        "xrun_events_last_60s": deque(),
        "started_mono": started_mono,
        "repeat_context": None,
    }

    stderr_log.write(
        f"{_local_log_ts()} [receiver] correlation_id={correlation_id} "
        f"resolved_alsa_device={alsa_device} port={port} udp_input={udp_input_url} "
        f"log_max_bytes={log_max_bytes} log_backups={log_backup_count}\n"
    )
    stderr_log.flush()

    _safe_log_system(
        "stream_receiver_started",
        {
            "correlation_id": correlation_id,
            "port": port,
            "alsa_device": alsa_device,
            "udp_input": udp_input_url,
            "started_at": _utc_iso_ms(),
        },
    )

    proc = subprocess.Popen(
        cmd,
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=0,
    )

    drain_thread = threading.Thread(
        target=_drain_ffmpeg_stderr,
        args=(proc.stderr, stderr_log, counters),
        kwargs={
            "correlation_id": correlation_id,
            "port": port,
            "alsa_device": alsa_device,
        },
        daemon=True,
    )
    drain_thread.start()

    cleanup_reason: Optional[str] = None
    cleanup_started = False
    shutdown_signal_num: Optional[int] = None
    shutdown_signal_name: Optional[str] = None

    def _cleanup(reason: str = "internal"):
        """Gracefully stop ffmpeg via stdin 'q' + SIGTERM."""
        nonlocal cleanup_reason, cleanup_started
        if cleanup_started:
            return
        cleanup_started = True
        cleanup_reason = reason
        stop_process(proc)

    def _cleanup_on_exit():
        _cleanup("atexit")

    atexit.register(_cleanup_on_exit)

    def _handle_signal(signum, frame):
        nonlocal shutdown_signal_num, shutdown_signal_name
        shutdown_signal_num = int(signum)
        try:
            shutdown_signal_name = signal.Signals(signum).name
        except Exception:
            shutdown_signal_name = str(signum)
        _cleanup("signal")

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    return_code: Optional[int] = None
    stderr_drain_timeout = False
    try:
        proc.wait()
        return_code = proc.returncode
    finally:
        # Keep this short to avoid manager-side forced kill before summary logging.
        drain_thread.join(timeout=0.5)
        stderr_drain_timeout = drain_thread.is_alive()
        if stderr_drain_timeout:
            stderr_log.write(
                f"{_local_log_ts()} [receiver] stderr_drain_timeout=1 "
                f"correlation_id={correlation_id}\n"
            )
            stderr_log.flush()

        duration_seconds = round(time.monotonic() - started_mono, 3)
        exit_class = _classify_receiver_exit(return_code, shutdown_signal_name)
        xrun_session_rate = (
            round((int(counters.get("alsa_xrun") or 0) / duration_seconds), 3)
            if duration_seconds > 0
            else 0.0
        )
        xrun_burst_rate = _calc_xrun_burst_rate_per_sec(counters)
        summary = {
            "correlation_id": correlation_id,
            "port": port,
            "alsa_device": alsa_device,
            "udp_overrun": counters["udp_overrun"],
            "alsa_xrun": counters["alsa_xrun"],
            "xrun_peak_1s": counters["xrun_peak_1s"],
            "xrun_peak_60s": counters["xrun_peak_60s"],
            "xrun_max_consecutive": counters["xrun_max_consecutive"],
            "xrun_session_rate_per_sec": xrun_session_rate,
            "xrun_burst_rate_per_sec": xrun_burst_rate,
            "xrun_underrun_count": counters["xrun_underrun_count"],
            "xrun_overrun_count": counters["xrun_overrun_count"],
            "xrun_unknown_count": counters["xrun_unknown_count"],
            "last_xrun_type": counters["last_xrun_type"],
            "last_xrun_type_source": counters["last_xrun_type_source"],
            "demux_errors": counters["demux_errors"],
            "immediate_exit": counters["immediate_exit"],
            "audio_device_errors": counters["audio_device_errors"],
            "connection_errors": counters["connection_errors"],
            "first_input_at": counters["first_input_at"],
            "first_output_at": counters["first_output_at"],
            "first_overrun_at": counters["first_overrun_at"],
            "last_overrun_at": counters["last_overrun_at"],
            "first_xrun_at": counters["first_xrun_at"],
            "last_xrun_at": counters["last_xrun_at"],
            "duration_seconds": duration_seconds,
            "return_code": return_code,
            "exit_class": exit_class,
            "shutdown_signal_num": shutdown_signal_num,
            "shutdown_signal": shutdown_signal_name,
            "cleanup_reason": cleanup_reason,
            "stderr_drain_timeout": stderr_drain_timeout,
            "ended_at": _utc_iso_ms(),
        }

        stderr_log.write(
            f"{_local_log_ts()} [receiver] summary correlation_id={correlation_id} "
            f"return_code={return_code} duration_seconds={duration_seconds} "
            f"exit_class={exit_class} shutdown_signal={shutdown_signal_name} "
            f"udp_overrun={counters['udp_overrun']} alsa_xrun={counters['alsa_xrun']} "
            f"xrun_peak_1s={counters['xrun_peak_1s']} "
            f"xrun_peak_60s={counters['xrun_peak_60s']} "
            f"xrun_max_consecutive={counters['xrun_max_consecutive']} "
            f"xrun_underrun={counters['xrun_underrun_count']} "
            f"xrun_overrun={counters['xrun_overrun_count']} "
            f"xrun_unknown={counters['xrun_unknown_count']} "
            f"last_xrun_type={counters['last_xrun_type']} "
            f"demux_errors={counters['demux_errors']} "
            f"immediate_exit={counters['immediate_exit']} "
            f"audio_device_errors={counters['audio_device_errors']} "
            f"connection_errors={counters['connection_errors']} "
            f"stderr_drain_timeout={int(stderr_drain_timeout)}\n"
        )
        stderr_log.flush()

        _write_xrun_status(counters, correlation_id, force=True)
        _safe_log_system("stream_receiver_summary", summary)
        quality_summary = _calc_stream_quality_summary(
            counters,
            duration_seconds=duration_seconds,
        )
        quality_summary["correlation_id"] = correlation_id
        quality_summary["xrun_underrun_count"] = counters["xrun_underrun_count"]
        quality_summary["xrun_overrun_count"] = counters["xrun_overrun_count"]
        quality_summary["xrun_unknown_count"] = counters["xrun_unknown_count"]
        quality_summary["last_xrun_type"] = counters["last_xrun_type"]
        quality_summary["last_xrun_type_source"] = counters["last_xrun_type_source"]
        _safe_log_system("stream_session_quality_summary", quality_summary)

        if stderr_drain_timeout:
            _safe_log_error(
                "stream_receiver_stderr_drain_timeout",
                {
                    "correlation_id": correlation_id,
                    "duration_seconds": duration_seconds,
                },
            )

        if counters["udp_overrun"] > 0:
            _safe_log_error(
                "stream_receiver_udp_overrun",
                {
                    "correlation_id": correlation_id,
                    "overrun_count": counters["udp_overrun"],
                    "duration_seconds": duration_seconds,
                },
            )

        if counters["alsa_xrun"] > 0:
            _safe_log_error(
                "stream_receiver_alsa_xrun",
                {
                    "correlation_id": correlation_id,
                    "xrun_count": counters["alsa_xrun"],
                    "xrun_underrun_count": counters["xrun_underrun_count"],
                    "xrun_overrun_count": counters["xrun_overrun_count"],
                    "xrun_unknown_count": counters["xrun_unknown_count"],
                    "last_xrun_type": counters["last_xrun_type"],
                    "last_xrun_type_source": counters["last_xrun_type_source"],
                    "duration_seconds": duration_seconds,
                },
            )

        if exit_class == "controlled":
            _safe_log_system(
                "stream_receiver_exit_controlled",
                {
                    "correlation_id": correlation_id,
                    "return_code": return_code,
                    "shutdown_signal": shutdown_signal_name,
                    "duration_seconds": duration_seconds,
                },
            )
        elif exit_class == "unexpected":
            _safe_log_error(
                "stream_receiver_exit_nonzero",
                {
                    "correlation_id": correlation_id,
                    "return_code": return_code,
                    "exit_class": exit_class,
                },
            )

        try:
            if proc.stderr:
                proc.stderr.close()
        except Exception:
            pass
        try:
            stderr_log.close()
        except Exception:
            pass


if __name__ == "__main__":
    main()
