"""
AnnounceFlow - Stream Receiver (V1)

Receives raw PCM audio over UDP and plays through ALSA via ffmpeg.
This script is spawned by StreamManager as a subprocess.

Audio format: s16le, 44100 Hz, mono
Transport: UDP on configurable port (default 5800)

Usage: python _stream_receiver.py [port] [alsa_device]
"""
import atexit
import os
import re
import shlex
import signal
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from typing import Any, Dict, Optional


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

    return f"udp://0.0.0.0:{port}?{'&'.join(params)}"


def _parse_extra_ffmpeg_args() -> list[str]:
    raw = os.environ.get("ANNOUNCEFLOW_STREAM_FFMPEG_ARGS", "").strip()
    if not raw:
        return []
    try:
        return shlex.split(raw)
    except ValueError:
        return []


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

    event_ts = _utc_iso_ms()
    log_file.write(f"{_local_log_ts()} {text}\n")
    log_file.flush()

    lower = text.lower()
    repeat_match = re.search(r"last message repeated\s+(\d+)\s+times", lower)
    if repeat_match:
        repeated_count = int(repeat_match.group(1))
        repeat_context = counters.get("repeat_context")
        if repeat_context == "udp_overrun":
            counters["udp_overrun"] += repeated_count
            counters["last_overrun_at"] = event_ts
        elif repeat_context == "alsa_xrun":
            counters["alsa_xrun"] += repeated_count
            counters["last_xrun_at"] = event_ts
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
    if "alsa buffer xrun" in lower:
        counters["alsa_xrun"] += 1
        counters["last_xrun_at"] = event_ts
        if counters.get("first_xrun_at") is None:
            counters["first_xrun_at"] = event_ts
        counters["repeat_context"] = "alsa_xrun"
    if "error during demuxing" in lower:
        counters["demux_errors"] += 1
    if "immediate exit requested" in lower:
        counters["immediate_exit"] += 1
    if "cannot open audio device" in lower or "device or resource busy" in lower:
        counters["audio_device_errors"] += 1
    if "connection refused" in lower or "timed out" in lower or "network is unreachable" in lower:
        counters["connection_errors"] += 1


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
    ]
    cmd.extend(_parse_extra_ffmpeg_args())
    cmd.extend(["-f", "alsa", alsa_device])

    # Log ffmpeg stderr with per-line timestamps for deterministic debugging.
    log_dir = os.environ.get("ANNOUNCEFLOW_LOG_DIR", "").strip()
    if not log_dir:
        log_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
    os.makedirs(log_dir, exist_ok=True)

    log_path = os.path.join(log_dir, "stream_receiver_ffmpeg.log")
    stderr_log = open(log_path, "a", encoding="utf-8", errors="replace", buffering=1)
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
        "repeat_context": None,
    }

    stderr_log.write(
        f"{_local_log_ts()} [receiver] correlation_id={correlation_id} "
        f"resolved_alsa_device={alsa_device} port={port} udp_input={udp_input_url}\n"
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
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=0,
        preexec_fn=os.setsid,
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

    def _cleanup():
        """Kill the entire process group to prevent orphan ffmpeg."""
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except (OSError, ProcessLookupError):
            pass

    atexit.register(_cleanup)

    def _handle_signal(signum, frame):
        _cleanup()

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
        summary = {
            "correlation_id": correlation_id,
            "port": port,
            "alsa_device": alsa_device,
            "udp_overrun": counters["udp_overrun"],
            "alsa_xrun": counters["alsa_xrun"],
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
            "stderr_drain_timeout": stderr_drain_timeout,
            "ended_at": _utc_iso_ms(),
        }

        stderr_log.write(
            f"{_local_log_ts()} [receiver] summary correlation_id={correlation_id} "
            f"return_code={return_code} duration_seconds={duration_seconds} "
            f"udp_overrun={counters['udp_overrun']} alsa_xrun={counters['alsa_xrun']} "
            f"demux_errors={counters['demux_errors']} "
            f"immediate_exit={counters['immediate_exit']} "
            f"audio_device_errors={counters['audio_device_errors']} "
            f"connection_errors={counters['connection_errors']} "
            f"stderr_drain_timeout={int(stderr_drain_timeout)}\n"
        )
        stderr_log.flush()

        _safe_log_system("stream_receiver_summary", summary)

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
                    "duration_seconds": duration_seconds,
                },
            )

        if return_code not in (None, 0, -15):
            _safe_log_error(
                "stream_receiver_exit_nonzero",
                {
                    "correlation_id": correlation_id,
                    "return_code": return_code,
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
