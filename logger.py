"""
AnnounceFlow - Event Logger
Comprehensive JSON Lines logging for system events, playback, triggers, and errors.

===============================================================================
LOG KONTROL KOMUTLARI (SSH ile Pi'ye bağlandıktan sonra kullan)
===============================================================================

# Pi'ye bağlan (şifresiz):
ssh admin@192.168.1.24

# Canlı log takibi (Ctrl+C ile çık):
tail -f /home/admin/announceflow/logs/events.jsonl

# Son 20 event:
tail -20 /home/admin/announceflow/logs/events.jsonl

# Tüm logları güzel formatlı göster:
cat /home/admin/announceflow/logs/events.jsonl | python3 -m json.tool --compact

# Sadece PLAY eventleri:
grep '"cat": "PLAY"' /home/admin/announceflow/logs/events.jsonl

# Sadece ERROR eventleri:
grep '"cat": "ERROR"' /home/admin/announceflow/logs/events.jsonl

# Bugünün logları (tarih formatı: 2026-02-03):
grep '2026-02-03' /home/admin/announceflow/logs/events.jsonl

# Klasik metin logları:
tail -100 /home/admin/announceflow/announceflow.log

# Servis durumu:
sudo systemctl status announceflow

===============================================================================
EVENT KATEGORİLERİ
===============================================================================
SYSTEM  : boot, shutdown, playlist_restore, playlist_restore_deferred
PLAY    : track_start, track_end, stop, playlist_set, playlist_end  
TRIGGER : one_time, recurring
PRAYER  : silence_start, silence_end, fetch, cache_hit, in_window, policy_decision
SCHEDULE: working_hours_start, working_hours_end, policy_decision, reconcile_resume
VOLUME  : change
WEB     : login, play, stop, volume, upload, delete
ERROR   : Hatalar ve uyarılar (örn: prayer_cache_corrupt, policy_fail_safe_engaged)

===============================================================================
"""
import json
import os
import logging
from datetime import datetime
from logging.handlers import RotatingFileHandler
from typing import Any, Dict, Optional


# Event categories
class EventCategory:
    SYSTEM = "SYSTEM"  # Boot, shutdown, service status
    PLAY = "PLAY"  # Track start/end, playlist events
    TRIGGER = "TRIGGER"  # Schedule triggers (one-time, recurring)
    PRAYER = "PRAYER"  # Prayer time silence periods
    SCHEDULE = "SCHEDULE"  # Working hours, scheduling decisions
    VOLUME = "VOLUME"  # Volume changes
    WEB = "WEB"  # Panel access, API calls
    ERROR = "ERROR"  # Errors and warnings


# Log directory
_DEFAULT_LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
LOG_DIR = os.environ.get("ANNOUNCEFLOW_LOG_DIR", "").strip() or _DEFAULT_LOG_DIR
EVENT_LOG_FILE = (
    os.environ.get("ANNOUNCEFLOW_EVENT_LOG_FILE", "").strip()
    or os.path.join(LOG_DIR, "events.jsonl")
)

# Ensure log directory exists
os.makedirs(LOG_DIR, exist_ok=True)

# Configure event file handler
_event_logger = logging.getLogger("events")
_event_logger.setLevel(logging.INFO)
_event_logger.propagate = False  # Don't propagate to root logger

# Remove existing handlers
if _event_logger.hasHandlers():
    _event_logger.handlers.clear()

# JSON Lines file handler (1MB, 5 backups)
_event_handler = RotatingFileHandler(
    EVENT_LOG_FILE, maxBytes=1_000_000, backupCount=5  # 1 MB
)
_event_handler.setFormatter(logging.Formatter("%(message)s"))
_event_logger.addHandler(_event_handler)


def log_event(category: str, event: str, data: Optional[Dict[str, Any]] = None) -> None:
    """
    Log an event in JSON Lines format.

    Args:
        category: Event category (SYSTEM, PLAY, TRIGGER, etc.)
        event: Event name (e.g., 'track_start', 'boot', 'silence_start')
        data: Optional additional data dictionary

    Example:
        log_event("PLAY", "track_start", {"file": "song.mp3", "index": 1})
    """
    entry = {
        "ts": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
        "cat": category,
        "event": event,
    }
    if data:
        entry["data"] = data

    _event_logger.info(json.dumps(entry, ensure_ascii=False))


def log_system(event: str, data: Optional[Dict[str, Any]] = None) -> None:
    """Log a SYSTEM event."""
    log_event(EventCategory.SYSTEM, event, data)


def log_play(event: str, data: Optional[Dict[str, Any]] = None) -> None:
    """Log a PLAY event."""
    log_event(EventCategory.PLAY, event, data)


def log_trigger(event: str, data: Optional[Dict[str, Any]] = None) -> None:
    """Log a TRIGGER event."""
    log_event(EventCategory.TRIGGER, event, data)


def log_prayer(event: str, data: Optional[Dict[str, Any]] = None) -> None:
    """Log a PRAYER event."""
    log_event(EventCategory.PRAYER, event, data)


def log_schedule(event: str, data: Optional[Dict[str, Any]] = None) -> None:
    """Log a SCHEDULE event."""
    log_event(EventCategory.SCHEDULE, event, data)


def log_volume(event: str, data: Optional[Dict[str, Any]] = None) -> None:
    """Log a VOLUME event."""
    log_event(EventCategory.VOLUME, event, data)


def log_web(event: str, data: Optional[Dict[str, Any]] = None) -> None:
    """Log a WEB event."""
    log_event(EventCategory.WEB, event, data)


def log_error(event: str, data: Optional[Dict[str, Any]] = None) -> None:
    """Log an ERROR event."""
    log_event(EventCategory.ERROR, event, data)


if __name__ == "__main__":
    # Test logging
    print(f"Log directory: {LOG_DIR}")
    print(f"Event log file: {EVENT_LOG_FILE}")

    log_system("test", {"message": "Logger test"})
    log_play("track_start", {"file": "test.mp3", "index": 1, "total": 5})
    log_volume("change", {"old": 50, "new": 75})

    print("Test events logged. Check events.jsonl")
