"""
Silence policy resolver for working-hours and prayer constraints.
"""
import logging
from datetime import datetime
from typing import Any, Dict, Optional, Callable, Tuple

logger = logging.getLogger(__name__)
PrayerTimesProvider = Callable[[str, str, bool], Tuple[Optional[Dict[str, Any]], str]]


def _is_time_within_window(curr_time, start_time, end_time) -> bool:
    """Check if a time is within a range, including overnight windows."""
    if start_time <= end_time:
        return start_time <= curr_time <= end_time
    return curr_time >= start_time or curr_time <= end_time


def is_within_working_hours(config: dict, now: Optional[datetime] = None) -> bool:
    """Check if current time is within working hours."""
    if not config.get("working_hours_enabled", False):
        return True

    start_str = str(config.get("working_hours_start", "09:00"))
    end_str = str(config.get("working_hours_end", "22:00"))

    try:
        current_dt = now or datetime.now()
        curr = current_dt.time()
        start = datetime.strptime(start_str, "%H:%M").time()
        end = datetime.strptime(end_str, "%H:%M").time()
        return _is_time_within_window(curr, start, end)
    except Exception as e:
        logger.error("Working hours check error: %s", e)
        return True


def _is_prayer_window_active(
    times: Dict[str, Any], buffer_minutes: int = 1, now: Optional[datetime] = None
) -> bool:
    current_dt = now or datetime.now()
    current_minutes = current_dt.hour * 60 + current_dt.minute

    for prayer_key in ["imsak", "gunes", "ogle", "ikindi", "aksam", "yatsi"]:
        prayer_time_str = str(times.get(prayer_key, "")).strip()
        if not prayer_time_str:
            continue

        try:
            h, m = map(int, prayer_time_str.split(":"))
            prayer_minutes = h * 60 + m
            start = prayer_minutes - buffer_minutes
            end = prayer_minutes + 5 + buffer_minutes
            if start <= current_minutes <= end:
                return True
        except (ValueError, AttributeError):
            continue

    return False


def resolve_silence_policy(
    config: dict,
    *,
    allow_network: bool,
    fail_safe_on_unknown: bool,
    now: Optional[datetime] = None,
    prayer_times_provider: Optional[PrayerTimesProvider] = None,
) -> Dict[str, Any]:
    """Resolve effective silence policy with metadata."""
    if not is_within_working_hours(config, now=now):
        return {
            "policy": "working_hours",
            "silence_active": True,
            "reason_code": "outside_working_hours",
            "source": "config",
            "fail_safe_applied": False,
        }

    if not config.get("prayer_times_enabled", False):
        return {
            "policy": "none",
            "silence_active": False,
            "reason_code": "prayer_disabled",
            "source": "config",
            "fail_safe_applied": False,
        }

    city = str(config.get("prayer_times_city", "")).strip()
    district = str(config.get("prayer_times_district", "")).strip() or "Merkez"

    if not city:
        decision = {
            "policy": "unknown",
            "silence_active": False,
            "reason_code": "prayer_city_missing",
            "source": "config",
            "fail_safe_applied": False,
        }
        if fail_safe_on_unknown:
            decision["silence_active"] = True
            decision["reason_code"] = "prayer_city_missing_fail_safe"
            decision["fail_safe_applied"] = True
        return decision

    try:
        provider = prayer_times_provider
        if provider is None:
            import prayer_times as pt

            provider = pt.get_prayer_times

        provider_result = provider(city, district, allow_network)
        if (
            isinstance(provider_result, tuple)
            and len(provider_result) >= 2
        ):
            times, source = provider_result[0], str(provider_result[1] or "none")
        else:
            times, source = provider_result, "none"
    except Exception as e:
        logger.error("Prayer policy resolve error: %s", e)
        times = None
        source = "none"

    if times:
        if _is_prayer_window_active(times, buffer_minutes=1, now=now):
            return {
                "policy": "prayer",
                "silence_active": True,
                "reason_code": "prayer_window_active",
                "source": source,
                "fail_safe_applied": False,
            }

        return {
            "policy": "none",
            "silence_active": False,
            "reason_code": "prayer_window_inactive",
            "source": source,
            "fail_safe_applied": False,
        }

    decision = {
        "policy": "unknown",
        "silence_active": False,
        "reason_code": "prayer_times_unavailable",
        "source": source,
        "fail_safe_applied": False,
    }
    if fail_safe_on_unknown:
        decision["silence_active"] = True
        decision["reason_code"] = "prayer_unknown_fail_safe"
        decision["fail_safe_applied"] = True
    return decision


def is_prayer_time_active(
    config: dict,
    *,
    allow_network: bool = True,
    fail_safe_on_unknown: bool = False,
    now: Optional[datetime] = None,
    prayer_times_provider: Optional[PrayerTimesProvider] = None,
) -> bool:
    """Compatibility helper for callers that need a boolean."""
    decision = resolve_silence_policy(
        config,
        allow_network=allow_network,
        fail_safe_on_unknown=fail_safe_on_unknown,
        now=now,
        prayer_times_provider=prayer_times_provider,
    )
    return bool(decision.get("silence_active", False) and decision.get("policy") != "working_hours")
