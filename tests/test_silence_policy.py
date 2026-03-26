"""Silence policy and watchdog regression tests."""

from __future__ import annotations

from datetime import datetime
from unittest.mock import patch

import prayer_times as pt
import scheduler as scheduler_module
from scheduler import Scheduler
from services.silence_policy import resolve_silence_policy


class DummyPlayer:
    def __init__(self):
        self.is_playing = False
        self._playlist_active = False
        self.stop_calls = 0
        self.apply_calls = []

    def stop(self):
        self.stop_calls += 1
        self.is_playing = False
        self._playlist_active = False
        return True

    def apply_playlist_state(self, **kwargs):
        self.apply_calls.append(kwargs)
        if kwargs.get("play_next"):
            self.is_playing = True
            self._playlist_active = True
        elif "runtime_active" in kwargs:
            self._playlist_active = bool(kwargs["runtime_active"])
        return True


def _base_config() -> dict:
    return {
        "working_hours_enabled": False,
        "prayer_times_enabled": True,
        "prayer_times_city": "Istanbul",
        "prayer_times_district": "Kadikoy",
    }


def test_atomic_cache_roundtrip(tmp_path, monkeypatch):
    cache_path = tmp_path / "prayer_times_cache.json"
    monkeypatch.setattr(pt, "CACHE_FILE", str(cache_path))

    payload = {
        "Istanbul_Kadikoy_2026-02-27": {
            "imsak": "05:40",
            "ogle": "13:12",
            "ikindi": "16:31",
            "aksam": "18:55",
            "yatsi": "20:14",
            "date": "2026-02-27",
        }
    }
    pt._save_cache(payload)
    assert pt._load_cache() == payload


def test_corrupt_cache_is_quarantined(tmp_path, monkeypatch):
    cache_path = tmp_path / "prayer_times_cache.json"
    monkeypatch.setattr(pt, "CACHE_FILE", str(cache_path))
    cache_path.write_text("{invalid-json", encoding="utf-8")

    loaded = pt._load_cache()
    assert loaded == {}
    corrupt_files = list(tmp_path.glob("prayer_times_cache.json.corrupt.*"))
    assert len(corrupt_files) == 1


def test_unknown_policy_falls_back_to_fail_safe():
    config = _base_config()
    decision = resolve_silence_policy(
        config,
        allow_network=False,
        fail_safe_on_unknown=True,
        prayer_times_provider=lambda _city, _district, _allow_network: (None, "none"),
    )

    assert decision["policy"] == "unknown"
    assert decision["silence_active"] is True
    assert decision["fail_safe_applied"] is True
    assert decision["reason_code"] == "prayer_unknown_fail_safe"
    assert decision["source"] == "none"


def test_policy_metadata_reports_cache_source_with_di_clock():
    config = _base_config()
    now = datetime(2026, 2, 27, 13, 12)
    times = {"ogle": "13:12"}

    decision = resolve_silence_policy(
        config,
        allow_network=False,
        fail_safe_on_unknown=True,
        now=now,
        prayer_times_provider=lambda _city, _district, _allow_network: (
            times,
            "cache_fresh",
        ),
    )

    assert decision["policy"] == "prayer"
    assert decision["silence_active"] is True
    assert decision["reason_code"] == "prayer_window_active"
    assert decision["source"] == "cache_fresh"
    assert decision["fail_safe_applied"] is False


def test_reconcile_watchdog_resumes_missed_restore():
    scheduler = Scheduler(check_interval_seconds=1)
    scheduler._reconcile_interval_seconds = 0
    scheduler._last_reconcile_monotonic = 0
    scheduler.defer_playlist_restore("prayer", {
        "playlist": ["/tmp/a.mp3", "/tmp/b.mp3"],
        "index": 0,
        "loop": True,
        "active": True,
    })

    player = DummyPlayer()
    decision = {
        "policy": "none",
        "silence_active": False,
        "reason_code": "prayer_window_inactive",
        "source": "cache_fresh",
        "fail_safe_applied": False,
    }

    with patch.object(
        scheduler_module.db, "get_playlist_state", return_value={"active": True}
    ):
        scheduler._run_reconcile_watchdog({}, player, decision)

    assert any(call.get("play_next") for call in player.apply_calls)
    assert scheduler._get_pause_state("prayer") is None
