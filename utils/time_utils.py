"""Timezone and datetime helpers for AnnounceFlow.

Canonical storage is UTC ISO-8601 with trailing ``Z``.
User-facing scheduling and rendering use local app timezone.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any, Optional

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover - Python < 3.9 fallback
    ZoneInfo = None  # type: ignore[assignment]


DEFAULT_TIMEZONE = "Europe/Istanbul"


def get_app_timezone_name() -> str:
    """Return configured app timezone name."""
    return (os.environ.get("ANNOUNCEFLOW_TIMEZONE", "").strip() or DEFAULT_TIMEZONE)


def get_app_timezone():
    """Return timezone object for configured app timezone."""
    tz_name = get_app_timezone_name()
    if ZoneInfo is None:
        return timezone.utc
    try:
        return ZoneInfo(tz_name)
    except Exception:
        return ZoneInfo(DEFAULT_TIMEZONE)


def now_local() -> datetime:
    """Return timezone-aware current local datetime."""
    return datetime.now(get_app_timezone())


def now_utc() -> datetime:
    """Return timezone-aware current UTC datetime."""
    return datetime.now(timezone.utc)


def ensure_local(value: datetime) -> datetime:
    """Ensure datetime is timezone-aware in local timezone."""
    if value.tzinfo is None:
        return value.replace(tzinfo=get_app_timezone())
    return value.astimezone(get_app_timezone())


def ensure_utc(value: datetime) -> datetime:
    """Ensure datetime is timezone-aware in UTC."""
    if value.tzinfo is None:
        return value.replace(tzinfo=get_app_timezone()).astimezone(timezone.utc)
    return value.astimezone(timezone.utc)


def parse_local_date_time(date_str: str, time_str: str) -> datetime:
    """Parse form date/time (YYYY-MM-DD + HH:MM) as local timezone."""
    parsed = datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M")
    return parsed.replace(tzinfo=get_app_timezone())


def _parse_datetime_text(raw_text: str) -> Optional[datetime]:
    text = str(raw_text or "").strip()
    if not text:
        return None

    normalized = text.replace(" ", "T")
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"

    try:
        return datetime.fromisoformat(normalized)
    except ValueError:
        pass

    for fmt in (
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%dT%H:%M",
    ):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def parse_storage_datetime_to_utc(
    raw_value: Any, *, naive_as_local: bool = True
) -> Optional[datetime]:
    """Parse DB datetime value and return timezone-aware UTC datetime."""
    if isinstance(raw_value, datetime):
        parsed = raw_value
    else:
        parsed = _parse_datetime_text(str(raw_value or ""))
    if parsed is None:
        return None

    if parsed.tzinfo is None:
        if naive_as_local:
            parsed = parsed.replace(tzinfo=get_app_timezone())
        else:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    return parsed.astimezone(timezone.utc)


def parse_storage_datetime_to_local(
    raw_value: Any, *, naive_as_local: bool = True
) -> Optional[datetime]:
    """Parse DB datetime value and return timezone-aware local datetime."""
    parsed_utc = parse_storage_datetime_to_utc(
        raw_value, naive_as_local=naive_as_local
    )
    if parsed_utc is None:
        return None
    return parsed_utc.astimezone(get_app_timezone())


def to_storage_utc_z(raw_value: Any) -> str:
    """Normalize datetime-like value into canonical UTC ``...Z`` string."""
    parsed_utc: Optional[datetime]
    if isinstance(raw_value, datetime):
        parsed_utc = ensure_utc(raw_value)
    else:
        parsed_utc = parse_storage_datetime_to_utc(raw_value, naive_as_local=True)

    if parsed_utc is None:
        raise ValueError("invalid datetime value for storage")

    return (
        parsed_utc.replace(microsecond=0)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )


def format_storage_datetime_local(
    raw_value: Any, *, fmt: str = "%d.%m.%Y %H:%M"
) -> Optional[str]:
    """Format DB datetime value for local display."""
    parsed_local = parse_storage_datetime_to_local(raw_value, naive_as_local=True)
    if parsed_local is None:
        return None
    return parsed_local.strftime(fmt)
