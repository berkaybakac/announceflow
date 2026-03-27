"""
Schedule conflict detection helpers.

Blocks overlapping plans based on media duration.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Dict, Iterable, List, Optional, Tuple

import database as db
from utils.time_utils import ensure_local, parse_storage_datetime_to_local


DEFAULT_DURATION_SECONDS = 120
SECONDS_PER_MINUTE = 60
MINUTES_PER_DAY = 24 * 60
SECONDS_PER_DAY = MINUTES_PER_DAY * SECONDS_PER_MINUTE
SECONDS_PER_WEEK = 7 * SECONDS_PER_DAY


def resolve_duration_seconds(media_id: int) -> int:
    """Resolve media duration with safe fallback."""
    try:
        media_id_int = int(media_id)
    except (TypeError, ValueError):
        return DEFAULT_DURATION_SECONDS

    media = db.get_media_file(media_id_int)
    if not media:
        return DEFAULT_DURATION_SECONDS

    try:
        duration = int(media.get("duration_seconds") or 0)
    except (TypeError, ValueError):
        duration = 0

    if duration <= 0:
        return DEFAULT_DURATION_SECONDS
    return duration


def intervals_overlap(a_start: int, a_end: int, b_start: int, b_end: int) -> bool:
    """Return True when [a_start, a_end) overlaps [b_start, b_end)."""
    return a_start < b_end and b_start < a_end


def parse_hhmm_to_minute(time_str: str) -> int:
    """Parse HH:MM into minute-of-day."""
    if not isinstance(time_str, str):
        raise ValueError("time must be a string")

    parts = time_str.strip().split(":")
    if len(parts) != 2:
        raise ValueError("invalid HH:MM")

    hour = int(parts[0])
    minute = int(parts[1])
    if hour < 0 or hour > 23 or minute < 0 or minute > 59:
        raise ValueError("invalid HH:MM")
    return hour * 60 + minute


def has_self_overlap_for_interval(duration_seconds: int, interval_minutes: int) -> bool:
    """Return True if recurring interval schedule overlaps with itself."""
    try:
        duration = int(duration_seconds)
        interval = int(interval_minutes)
    except (TypeError, ValueError):
        return False

    if duration <= 0 or interval <= 0:
        return False
    return duration > interval * SECONDS_PER_MINUTE


def _normalize_days(days_raw: Iterable) -> List[int]:
    days = []
    for day in days_raw or []:
        try:
            day_int = int(day)
        except (TypeError, ValueError):
            continue
        if 0 <= day_int <= 6 and day_int not in days:
            days.append(day_int)
    return sorted(days)


def _parse_specific_times(raw) -> List[str]:
    if raw is None:
        return []
    if isinstance(raw, list):
        return [str(x).strip() for x in raw if str(x).strip()]
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return []
        if text.startswith("["):
            try:
                parsed = json.loads(text)
                if isinstance(parsed, list):
                    return [str(x).strip() for x in parsed if str(x).strip()]
            except json.JSONDecodeError:
                pass
        return [t.strip() for t in text.split(",") if t.strip()]
    return []


def _parse_schedule_datetime(raw_value: str) -> Optional[datetime]:
    return parse_storage_datetime_to_local(raw_value, naive_as_local=True)


def _generate_interval_triggers_for_day(
    start_minute: int, end_minute: int, interval_minutes: int
) -> List[int]:
    if interval_minutes <= 0:
        return []

    if start_minute <= end_minute:
        window_minutes = end_minute - start_minute
    else:
        window_minutes = (MINUTES_PER_DAY - start_minute) + end_minute

    triggers = []
    offset = 0
    while offset <= window_minutes:
        trigger_minute = (start_minute + offset) % MINUTES_PER_DAY
        triggers.append(trigger_minute)
        offset += interval_minutes
    return triggers


def expand_recurring_triggers_for_week(schedule: Dict) -> List[int]:
    """
    Expand recurring schedule into minute-of-week trigger points.

    Matches current scheduler semantics for specific and interval modes.
    """
    days = _normalize_days(schedule.get("days_of_week", []))
    if not days:
        return []

    specific_times = _parse_specific_times(schedule.get("specific_times"))
    week_minutes: List[int] = []

    if specific_times:
        parsed_times = []
        for time_str in specific_times:
            try:
                parsed_times.append(parse_hhmm_to_minute(time_str))
            except ValueError:
                continue

        for day in days:
            for minute_of_day in parsed_times:
                week_minutes.append(day * MINUTES_PER_DAY + minute_of_day)
    else:
        try:
            start_minute = parse_hhmm_to_minute(schedule.get("start_time", "00:00"))
            end_minute = parse_hhmm_to_minute(schedule.get("end_time", "23:59"))
            interval = int(schedule.get("interval_minutes") or 0)
        except (ValueError, TypeError):
            return []

        day_triggers = _generate_interval_triggers_for_day(
            start_minute, end_minute, interval
        )
        for day in days:
            for minute_of_day in day_triggers:
                week_minutes.append(day * MINUTES_PER_DAY + minute_of_day)

    return sorted(set(week_minutes))


def build_weekly_intervals(
    schedule: Dict, duration_seconds: int
) -> List[Tuple[int, int]]:
    """Build half-open weekly intervals [start, end) for recurring schedule triggers."""
    try:
        duration = int(duration_seconds)
    except (TypeError, ValueError):
        duration = DEFAULT_DURATION_SECONDS
    if duration <= 0:
        duration = DEFAULT_DURATION_SECONDS

    if duration >= SECONDS_PER_WEEK:
        return [(0, SECONDS_PER_WEEK)]

    intervals: List[Tuple[int, int]] = []
    for trigger_minute in expand_recurring_triggers_for_week(schedule):
        start_sec = trigger_minute * SECONDS_PER_MINUTE
        end_sec = start_sec + duration
        if end_sec <= SECONDS_PER_WEEK:
            intervals.append((start_sec, end_sec))
        else:
            intervals.append((start_sec, SECONDS_PER_WEEK))
            intervals.append((0, end_sec - SECONDS_PER_WEEK))

    intervals.sort(key=lambda item: item[0])
    return intervals


def _build_one_time_weekly_intervals(
    start_dt: datetime, duration_seconds: int
) -> List[Tuple[int, int]]:
    try:
        duration = int(duration_seconds)
    except (TypeError, ValueError):
        duration = DEFAULT_DURATION_SECONDS
    if duration <= 0:
        duration = DEFAULT_DURATION_SECONDS

    if duration >= SECONDS_PER_WEEK:
        return [(0, SECONDS_PER_WEEK)]

    start_of_week = (
        start_dt.weekday() * SECONDS_PER_DAY
        + start_dt.hour * 3600
        + start_dt.minute * 60
        + start_dt.second
    )
    end_of_week = start_of_week + duration
    if end_of_week <= SECONDS_PER_WEEK:
        return [(start_of_week, end_of_week)]
    return [(start_of_week, SECONDS_PER_WEEK), (0, end_of_week - SECONDS_PER_WEEK)]


def _has_any_overlap(
    intervals_a: List[Tuple[int, int]], intervals_b: List[Tuple[int, int]]
) -> bool:
    for a_start, a_end in intervals_a:
        for b_start, b_end in intervals_b:
            if intervals_overlap(a_start, a_end, b_start, b_end):
                return True
    return False


def find_conflict_for_one_time(
    candidate_dt: datetime, media_id: int, exclude_one_time_id: Optional[int] = None
) -> Optional[Dict]:
    """Find first conflict for a candidate one-time schedule."""
    candidate_local = ensure_local(candidate_dt)
    candidate_duration = resolve_duration_seconds(media_id)
    candidate_end = candidate_local + timedelta(seconds=candidate_duration)

    # 1) Pending one-time schedules (absolute datetime overlap).
    for existing in db.get_pending_one_time_schedules():
        existing_id = existing.get("id")
        if exclude_one_time_id is not None and existing_id == exclude_one_time_id:
            continue

        existing_dt = _parse_schedule_datetime(existing.get("scheduled_datetime", ""))
        if not existing_dt:
            continue

        existing_duration = resolve_duration_seconds(existing.get("media_id"))
        existing_end = existing_dt + timedelta(seconds=existing_duration)
        if candidate_local < existing_end and existing_dt < candidate_end:
            return {
                "type": "one_time",
                "schedule_id": existing_id,
                "media_id": existing.get("media_id"),
            }

    # 2) Active recurring schedules (weekly slot overlap).
    candidate_weekly = _build_one_time_weekly_intervals(
        candidate_local, candidate_duration
    )
    for recurring in db.get_active_recurring_schedules():
        recurring_duration = resolve_duration_seconds(recurring.get("media_id"))
        recurring_weekly = build_weekly_intervals(recurring, recurring_duration)
        if _has_any_overlap(candidate_weekly, recurring_weekly):
            return {
                "type": "recurring",
                "schedule_id": recurring.get("id"),
                "media_id": recurring.get("media_id"),
            }

    return None


def find_conflict_for_recurring(
    candidate_recurring: Dict, exclude_recurring_id: Optional[int] = None
) -> Optional[Dict]:
    """Find first conflict for a candidate recurring schedule."""
    candidate_duration = resolve_duration_seconds(candidate_recurring.get("media_id"))
    candidate_weekly = build_weekly_intervals(candidate_recurring, candidate_duration)
    if not candidate_weekly:
        return None

    # 1) Active recurring schedules.
    for recurring in db.get_active_recurring_schedules():
        recurring_id = recurring.get("id")
        if exclude_recurring_id is not None and recurring_id == exclude_recurring_id:
            continue

        recurring_duration = resolve_duration_seconds(recurring.get("media_id"))
        recurring_weekly = build_weekly_intervals(recurring, recurring_duration)
        if _has_any_overlap(candidate_weekly, recurring_weekly):
            return {
                "type": "recurring",
                "schedule_id": recurring_id,
                "media_id": recurring.get("media_id"),
            }

    # 2) Pending one-time schedules.
    for one_time in db.get_pending_one_time_schedules():
        one_time_dt = _parse_schedule_datetime(one_time.get("scheduled_datetime", ""))
        if not one_time_dt:
            continue

        one_time_duration = resolve_duration_seconds(one_time.get("media_id"))
        one_time_weekly = _build_one_time_weekly_intervals(one_time_dt, one_time_duration)
        if _has_any_overlap(candidate_weekly, one_time_weekly):
            return {
                "type": "one_time",
                "schedule_id": one_time.get("id"),
                "media_id": one_time.get("media_id"),
            }

    return None
