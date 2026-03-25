"""Audio alert evaluator for stream error visibility in the panel.

Reads recent `events.jsonl` rows and returns a compact alert envelope:
`ok | warn | critical`.
"""
from __future__ import annotations

import json
import os
from collections import deque
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

from logger import EVENT_LOG_FILE

DEFAULT_WINDOW_MINUTES = 10
MIN_WINDOW_MINUTES = 1
MAX_WINDOW_MINUTES = 120
DEFAULT_TAIL_LINES = 4000

CRITICAL_EVENTS = {
    "stream_receiver_died",
    "stream_receiver_exit_nonzero",
    "stream_receiver_stderr_drain_timeout",
}

WARN_EVENT_THRESHOLDS = {
    "stream_receiver_alsa_xrun": 3,
    "stream_receiver_udp_overrun": 1,
}

_WARN_EVENT_COUNT_KEYS = {
    "stream_receiver_alsa_xrun": "xrun_count",
    "stream_receiver_udp_overrun": "overrun_count",
}

_CRITICAL_REASON_TEXT = {
    "stream_receiver_died": "Alıcı beklenmedik şekilde durdu",
    "stream_receiver_exit_nonzero": "Alıcı beklenmedik hata ile kapandı",
    "stream_receiver_stderr_drain_timeout": "Alıcı kapanışında stderr zaman aşımı oluştu",
}

_WARN_REASON_TEXT = {
    "stream_receiver_alsa_xrun": "ALSA XRUN arttı",
    "stream_receiver_udp_overrun": "UDP overrun tespit edildi",
}


def clamp_window_minutes(value: Any) -> int:
    """Clamp window size into safe bounds."""
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return DEFAULT_WINDOW_MINUTES
    return max(MIN_WINDOW_MINUTES, min(MAX_WINDOW_MINUTES, parsed))


def _parse_ts(raw: Any) -> Optional[datetime]:
    text = str(raw or "").strip()
    if not text:
        return None
    if " " in text and "T" not in text:
        text = text.replace(" ", "T")
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _tail_lines(path: str, max_lines: int) -> List[str]:
    if not path or not os.path.isfile(path):
        return []
    max_lines = max(1, int(max_lines))
    ring: "deque[str]" = deque(maxlen=max_lines)
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                stripped = line.strip()
                if stripped:
                    ring.append(stripped)
    except OSError:
        return []
    return list(ring)


def _iter_recent_events(
    path: str, *, cutoff: datetime, max_lines: int
) -> Iterable[Tuple[datetime, Dict[str, Any]]]:
    for line in _tail_lines(path, max_lines=max_lines):
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, dict):
            continue
        ts = _parse_ts(payload.get("ts"))
        if ts is None or ts < cutoff:
            continue
        yield ts, payload


def _extract_warn_increment(event_name: str, payload: Dict[str, Any]) -> int:
    data = payload.get("data")
    if not isinstance(data, dict):
        return 1
    count_key = _WARN_EVENT_COUNT_KEYS.get(event_name)
    if not count_key:
        return 1
    raw_value = data.get(count_key)
    try:
        parsed = int(raw_value)
    except (TypeError, ValueError):
        return 1
    return max(1, parsed)


def _iso_or_none(value: Optional[datetime]) -> Optional[str]:
    if value is None:
        return None
    return value.isoformat(timespec="seconds").replace("+00:00", "Z")


def get_audio_alerts(
    *,
    window_minutes: Any = DEFAULT_WINDOW_MINUTES,
    events_file: Optional[str] = None,
    max_lines: int = DEFAULT_TAIL_LINES,
    now_utc: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Build audio alert envelope from recent stream events."""
    window = clamp_window_minutes(window_minutes)
    now = now_utc.astimezone(timezone.utc) if now_utc else datetime.now(timezone.utc)
    cutoff = now - timedelta(minutes=window)
    source_file = (events_file or EVENT_LOG_FILE or "").strip()

    tracked_events = sorted(CRITICAL_EVENTS | set(WARN_EVENT_THRESHOLDS.keys()))
    counts: Dict[str, int] = {name: 0 for name in tracked_events}
    critical_hits: Dict[str, int] = {name: 0 for name in CRITICAL_EVENTS}
    last_event_at: Optional[datetime] = None

    for event_ts, payload in _iter_recent_events(
        source_file, cutoff=cutoff, max_lines=max_lines
    ):
        event_name = str(payload.get("event") or "").strip()
        if not event_name:
            continue
        if event_name in CRITICAL_EVENTS:
            counts[event_name] += 1
            critical_hits[event_name] += 1
            if last_event_at is None or event_ts > last_event_at:
                last_event_at = event_ts
            continue
        if event_name in WARN_EVENT_THRESHOLDS:
            counts[event_name] += _extract_warn_increment(event_name, payload)
            if last_event_at is None or event_ts > last_event_at:
                last_event_at = event_ts

    reasons: List[str] = []
    level = "ok"

    for event_name in sorted(CRITICAL_EVENTS):
        hit_count = critical_hits[event_name]
        if hit_count > 0:
            reasons.append(f"{_CRITICAL_REASON_TEXT[event_name]} ({hit_count}x)")
    if reasons:
        level = "critical"

    warn_reasons: List[str] = []
    for event_name in sorted(WARN_EVENT_THRESHOLDS.keys()):
        total = counts[event_name]
        threshold = WARN_EVENT_THRESHOLDS[event_name]
        if total >= threshold:
            warn_reasons.append(
                f"{_WARN_REASON_TEXT[event_name]} ({total} / eşik {threshold})"
            )
    if warn_reasons and level == "ok":
        level = "warn"
    reasons.extend(warn_reasons)

    return {
        "level": level,
        "reasons": reasons,
        "last_event_ts": _iso_or_none(last_event_at),
        "window_minutes": window,
        "counts": counts,
    }

