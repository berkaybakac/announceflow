"""
Slot map service: builds occupied time-slot data for timeline visualization.

Combines one-time schedules, recurring schedules, and prayer time windows
into a unified slot list so the UI can show which times are available.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, NamedTuple, Optional, Tuple

import database as db
from services.config_service import load_config
from services.schedule_conflict_service import (
    MINUTES_PER_DAY,
    SECONDS_PER_MINUTE,
    expand_recurring_triggers_for_week,
    resolve_duration_seconds,
    _parse_schedule_datetime,
)
from utils.time_utils import now_local

logger = logging.getLogger(__name__)

# Prayer window: 1 min before ezan, ezan ~5 min, 1 min after
PRAYER_BUFFER_BEFORE = 1
PRAYER_DURATION_AFTER = 6  # 5 min ezan + 1 min buffer

PRAYER_LABELS = {
    "imsak": "Sabah",
    "ogle": "Öğle",
    "ikindi": "İkindi",
    "aksam": "Akşam",
    "yatsi": "Yatsı",
}

PRAYER_SLOT_PREFIX = "Sessiz"                    # System silences during prayer windows
PRAYER_SLOT_SUFFIX = "anons etkilenmez"  # One-time announcements still fire

# end_minute MAY exceed MINUTES_PER_DAY for overnight slots
class RawSlot(NamedTuple):
    start_minute: int
    end_minute: int
    slot_type: str
    label: str
    media_id: Optional[int] = None
    source_type: Optional[str] = None
    source_id: Optional[int] = None
    group_key: Optional[str] = None


def _minutes_to_hhmm(minutes: int) -> str:
    """Convert minute-of-day to HH:MM string.

    Clamps to [0, 23:59]. Callers must split overnight
    slots before calling this.
    """
    minutes = max(0, min(minutes, MINUTES_PER_DAY - 1))
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def _split_at_midnight(raw_slots: List[RawSlot]) -> Tuple[List[RawSlot], List[RawSlot]]:
    """Split raw slots at midnight boundary.

    Returns (today_slots, overflow_slots).
    overflow_slots have start/end relative to the NEXT day (0-based).
    """
    today: List[RawSlot] = []
    overflow: List[RawSlot] = []

    for slot in raw_slots:
        if slot.end_minute <= MINUTES_PER_DAY:
            today.append(slot)
        else:
            # Part on today: start → end of day
            today.append(slot._replace(end_minute=MINUTES_PER_DAY - 1))
            # Part on tomorrow: 0 → overflow minutes
            overflow.append(slot._replace(
                start_minute=0,
                end_minute=slot.end_minute - MINUTES_PER_DAY,
            ))

    return today, overflow


def _raw_to_dict(raw: RawSlot) -> Dict[str, Any]:
    """Convert a raw slot tuple to API dict."""
    item: Dict[str, Any] = {
        "start": _minutes_to_hhmm(raw.start_minute),
        "end": _minutes_to_hhmm(raw.end_minute),
        "type": raw.slot_type,
        "label": raw.label,
    }
    if raw.media_id is not None:
        item["media_id"] = raw.media_id
    if raw.source_type:
        item["source_type"] = raw.source_type
    if raw.source_id is not None:
        item["source_id"] = raw.source_id
    if raw.group_key:
        item["group_key"] = raw.group_key
    return item


# ── Prayer helpers ──

def _resolve_prayer_times(
    config: dict, date_str: str
) -> Optional[Dict[str, Any]]:
    """Resolve prayer times from cache for a given date."""
    city = str(config.get("prayer_times_city", "")).strip()
    district = str(config.get("prayer_times_district", "")).strip() or "Merkez"
    if not city:
        return None

    import prayer_times as pt

    cache_key = f"{city}_{district}_{date_str}"
    cache = pt._load_cache()
    times = cache.get(cache_key)

    if not times:
        times_result, _ = pt.get_prayer_times(city, district, allow_network=False)
        if times_result and times_result.get("date") == date_str:
            times = times_result

    return times if isinstance(times, dict) else None


def _prayer_time_to_raw(label: str, time_str: str) -> Optional[RawSlot]:
    """Convert a single prayer time entry to a raw slot."""
    time_str = time_str.strip()
    if not time_str:
        return None
    try:
        h, m = map(int, time_str.split(":"))
    except (ValueError, AttributeError):
        return None

    prayer_minute = h * 60 + m
    start = max(0, prayer_minute - PRAYER_BUFFER_BEFORE)
    end = prayer_minute + PRAYER_DURATION_AFTER
    full_label = f"{PRAYER_SLOT_PREFIX} - {label} ({time_str}) — {PRAYER_SLOT_SUFFIX}"
    return RawSlot(start, end, "prayer", full_label, source_type="prayer")


def _get_prayer_raw(config: dict, date_str: str) -> List[RawSlot]:
    """Get raw prayer time slots for a specific date."""
    if not config.get("prayer_times_enabled", False):
        return []

    try:
        times = _resolve_prayer_times(config, date_str)
    except (ImportError, OSError) as e:
        logger.warning("Prayer slots error for %s: %s", date_str, e)
        return []

    if not times:
        logger.debug("No prayer times cached for %s", date_str)
        return []

    raw: List[RawSlot] = []
    for key, label in PRAYER_LABELS.items():
        slot = _prayer_time_to_raw(label, str(times.get(key, "")))
        if slot:
            raw.append(slot)
    return raw


# ── Schedule helpers ──

def _get_one_time_raw(target_date: datetime) -> List[RawSlot]:
    """Get raw one-time schedule slots for a specific date."""
    raw: List[RawSlot] = []
    for schedule in db.get_pending_one_time_schedules():
        raw_dt = schedule.get("scheduled_datetime", "")
        sched_dt = _parse_schedule_datetime(raw_dt)
        if not sched_dt:
            if raw_dt:
                logger.warning("Skipping schedule id=%s: unparseable datetime=%r", schedule.get("id"), raw_dt)
            continue
        if sched_dt.date() != target_date.date():
            continue

        duration = resolve_duration_seconds(schedule.get("media_id", 0))
        if duration <= 0:
            logger.warning("Schedule id=%s has zero duration (media_id=%s)", schedule.get("id"), schedule.get("media_id"))
        start = sched_dt.hour * 60 + sched_dt.minute
        end = start + (duration // SECONDS_PER_MINUTE)
        if duration % SECONDS_PER_MINUTE:
            end += 1

        schedule_id = schedule.get("id")
        sid = int(schedule_id) if schedule_id is not None else None
        raw.append(RawSlot(
            start_minute=start,
            end_minute=end,
            slot_type="one_time",
            label=schedule.get("filename", ""),
            media_id=int(schedule.get("media_id", 0) or 0),
            source_type="one_time",
            source_id=sid,
            group_key=f"one_time:{sid}" if sid is not None else None,
        ))
    return raw


def _get_recurring_raw(target_weekday: int) -> List[RawSlot]:
    """Get raw recurring schedule slots for a specific weekday."""
    raw: List[RawSlot] = []

    for schedule in db.get_active_recurring_schedules():
        duration = resolve_duration_seconds(schedule.get("media_id", 0))
        dur_min = duration // SECONDS_PER_MINUTE
        if duration % SECONDS_PER_MINUTE:
            dur_min += 1

        filename = schedule.get("filename", "")
        schedule_id = schedule.get("id")
        media_id = int(schedule.get("media_id", 0) or 0)
        sid = int(schedule_id) if schedule_id is not None else None
        for trigger in expand_recurring_triggers_for_week(schedule):
            if trigger // MINUTES_PER_DAY != target_weekday:
                continue
            start = trigger % MINUTES_PER_DAY
            raw.append(RawSlot(
                start_minute=start,
                end_minute=start + dur_min,
                slot_type="recurring",
                label=filename,
                media_id=media_id,
                source_type="recurring",
                source_id=sid,
                group_key=f"recurring:{sid}" if sid is not None else None,
            ))

    return raw


# ── Public API ──

def get_day_slots(date_str: str) -> Dict[str, Any]:
    """Build complete slot map for a single day.

    Handles overnight slots: checks previous day for overflow
    into today, and splits today's slots at midnight.
    """
    try:
        target_date = datetime.strptime(date_str, "%Y-%m-%d")
    except (ValueError, TypeError):
        logger.warning("Invalid date_str=%r, falling back to today", date_str)
        target_date = now_local()
        date_str = target_date.strftime("%Y-%m-%d")

    config = load_config()
    weekday = target_date.weekday()

    working_hours = {
        "enabled": bool(config.get("working_hours_enabled", False)),
        "start": str(config.get("working_hours_start", "09:00")),
        "end": str(config.get("working_hours_end", "22:00")),
    }

    # Today's raw slots
    today_raw: List[RawSlot] = []
    today_raw.extend(_get_one_time_raw(target_date))
    today_raw.extend(_get_recurring_raw(weekday))
    today_raw.extend(_get_prayer_raw(config, date_str))

    today_slots, _ = _split_at_midnight(today_raw)

    # Previous day overflow into today
    prev_date = target_date - timedelta(days=1)
    prev_weekday = prev_date.weekday()
    prev_date_str = prev_date.strftime("%Y-%m-%d")

    prev_raw: List[RawSlot] = []
    prev_raw.extend(_get_one_time_raw(prev_date))
    prev_raw.extend(_get_recurring_raw(prev_weekday))
    prev_raw.extend(_get_prayer_raw(config, prev_date_str))

    _, overflow_into_today = _split_at_midnight(prev_raw)
    if overflow_into_today:
        logger.info("Overnight overflow: %d slot(s) from %s into %s", len(overflow_into_today), prev_date_str, date_str)
    today_slots.extend(overflow_into_today)

    # Convert to API dicts and sort
    slots = [_raw_to_dict(r) for r in today_slots]
    slots.sort(key=lambda s: s["start"])

    return {
        "date": date_str,
        "day_of_week": weekday,
        "working_hours": working_hours,
        "slots": slots,
    }


def get_week_slots(date_str: Optional[str] = None) -> Dict[str, Any]:
    """Build slot maps for a full week (Monday–Sunday)."""
    try:
        ref_date = datetime.strptime(date_str, "%Y-%m-%d") if date_str else now_local()
    except (ValueError, TypeError):
        ref_date = now_local()

    monday = ref_date - timedelta(days=ref_date.weekday())

    days = []
    for i in range(7):
        day_date = monday + timedelta(days=i)
        days.append(get_day_slots(day_date.strftime("%Y-%m-%d")))

    return {"days": days}
