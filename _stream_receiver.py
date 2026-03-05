"""
AnnounceFlow - Stream Receiver (V1)

Receives raw PCM audio over UDP and plays through ALSA via ffmpeg.
This script is spawned by StreamManager as a subprocess.

Audio format: s16le, 44100 Hz, mono
Transport: UDP on configurable port (default 5800)

Usage: python _stream_receiver.py [port] [alsa_device]
"""
import os
import re
import signal
import subprocess
import sys


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
            m = re.search(r"card\s+(\d+)\s*:\s*([^\[]+)\[([^\]]+)\]", line, re.IGNORECASE)
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
        _detect_preferred_card_candidates()
        + ["plughw:2,0", "plughw:0,0", "default"]
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


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 5800
    ffmpeg_bin = _find_ffmpeg()
    alsa_device = _resolve_alsa_device()

    cmd = [
        ffmpeg_bin,
        "-y",
        "-f", "s16le",
        "-ar", "44100",
        "-ac", "1",
        "-i", f"udp://0.0.0.0:{port}?overrun_nonfatal=1",
        "-f", "alsa",
        alsa_device,
    ]

    # Log ffmpeg stderr for debugging.
    # Tests can isolate this via ANNOUNCEFLOW_LOG_DIR.
    log_dir = os.environ.get("ANNOUNCEFLOW_LOG_DIR", "").strip()
    if not log_dir:
        log_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
    os.makedirs(log_dir, exist_ok=True)
    stderr_log = open(os.path.join(log_dir, "stream_receiver_ffmpeg.log"), "a")
    try:
        stderr_log.write(
            f"[receiver] resolved_alsa_device={alsa_device} port={port}\n"
        )
        stderr_log.flush()
    except Exception:
        pass

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=stderr_log,
        preexec_fn=os.setsid,
    )

    def _cleanup():
        """Kill the entire process group to prevent orphan ffmpeg."""
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except (OSError, ProcessLookupError):
            pass

    import atexit
    atexit.register(_cleanup)

    def _handle_signal(signum, frame):
        _cleanup()
        sys.exit(0)

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    proc.wait()


if __name__ == "__main__":
    main()
