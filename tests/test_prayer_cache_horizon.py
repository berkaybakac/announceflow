"""Prayer cache horizon and resilience tests."""

from __future__ import annotations

import json
from datetime import datetime, timedelta

import prayer_times as pt


def _cache_key(city: str, district: str, date_obj: datetime) -> str:
    return f"{city}_{district}_{date_obj.strftime('%Y-%m-%d')}"


def test_fetch_prunes_to_configured_cache_horizon(tmp_path, monkeypatch):
    cache_path = tmp_path / "prayer_times_cache.json"
    monkeypatch.setattr(pt, "CACHE_FILE", str(cache_path))
    monkeypatch.setattr(pt, "CACHE_HORIZON_DAYS", 30)
    monkeypatch.setattr(pt, "MAX_FETCH_DAYS", 30)
    monkeypatch.setattr(pt, "_get_district_id", lambda _city, _district: "34")

    start_date = datetime(2026, 1, 1)
    payload = []
    for i in range(45):
        d = start_date + timedelta(days=i)
        payload.append(
            {
                "MiladiTarihKisa": d.strftime("%d.%m.%Y"),
                "Imsak": "05:00",
                "Gunes": "06:30",
                "Ogle": "12:30",
                "Ikindi": "15:30",
                "Aksam": "18:00",
                "Yatsi": "19:30",
            }
        )

    class _DummyResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_val, exc_tb):
            return False

        def read(self):
            return json.dumps(payload).encode("utf-8")

    monkeypatch.setattr(pt.urllib.request, "urlopen", lambda _req, timeout=15: _DummyResponse())

    assert pt.fetch_weekly_prayer_times("Istanbul", "Kadikoy") is True
    cache = pt._load_cache()
    prefix = "Istanbul_Kadikoy_"
    keys = sorted(key for key in cache if key.startswith(prefix))
    assert len(keys) == pt.CACHE_HORIZON_DAYS


def test_exact_date_hit_and_stale_horizon_behavior(tmp_path, monkeypatch):
    cache_path = tmp_path / "prayer_times_cache.json"
    monkeypatch.setattr(pt, "CACHE_FILE", str(cache_path))
    monkeypatch.setattr(pt, "STALE_FALLBACK_DAYS", 7)

    city = "Istanbul"
    district = "Kadikoy"
    now = datetime.now()
    today_key = _cache_key(city, district, now)

    cache = {
        today_key: {"ogle": "12:30", "date": now.strftime("%Y-%m-%d")},
    }
    pt._save_cache(cache)

    exact, source = pt.get_prayer_times(city, district, allow_network=False)
    assert source == "cache_fresh"
    assert exact is not None
    assert exact.get("date") == now.strftime("%Y-%m-%d")

    stale_in_horizon = now - timedelta(days=5)
    stale_in_horizon_key = _cache_key(city, district, stale_in_horizon)
    pt._save_cache(
        {
            stale_in_horizon_key: {
                "ogle": "12:30",
                "date": stale_in_horizon.strftime("%Y-%m-%d"),
            }
        }
    )
    stale, stale_source = pt.get_prayer_times(city, district, allow_network=False)
    assert stale_source == "cache_stale"
    assert stale is not None

    stale_out_of_horizon = now - timedelta(days=pt.STALE_FALLBACK_DAYS + 2)
    stale_out_of_horizon_key = _cache_key(city, district, stale_out_of_horizon)
    pt._save_cache(
        {
            stale_out_of_horizon_key: {
                "ogle": "12:30",
                "date": stale_out_of_horizon.strftime("%Y-%m-%d"),
            }
        }
    )
    missing, missing_source = pt.get_prayer_times(city, district, allow_network=False)
    assert missing is None
    assert missing_source == "none"


def test_corrupt_cache_is_quarantined(tmp_path, monkeypatch):
    cache_path = tmp_path / "prayer_times_cache.json"
    monkeypatch.setattr(pt, "CACHE_FILE", str(cache_path))
    cache_path.write_text("{invalid-json", encoding="utf-8")

    loaded = pt._load_cache()
    assert loaded == {}
    corrupt_files = list(tmp_path.glob("prayer_times_cache.json.corrupt.*"))
    assert len(corrupt_files) == 1


def test_aladhan_fallback_caches_current_day(tmp_path, monkeypatch):
    cache_path = tmp_path / "prayer_times_cache.json"
    monkeypatch.setattr(pt, "CACHE_FILE", str(cache_path))
    monkeypatch.setattr(pt, "CACHE_HORIZON_DAYS", 370)
    monkeypatch.setattr(pt, "fetch_weekly_prayer_times", lambda _city, _district: False)
    monkeypatch.setattr(pt, "fetch_collectapi_prayer_times", lambda _city, _district: False)
    monkeypatch.setattr(pt, "_network_retry_after", {})
    monkeypatch.setattr(pt, "_last_unavailable_log_at", {})

    today = datetime.now()
    payload = {
        "data": [
            {
                "date": {"gregorian": {"date": today.strftime("%d-%m-%Y")}},
                "timings": {
                    "Fajr": "04:01 (+03)",
                    "Sunrise": "05:30 (+03)",
                    "Dhuhr": "12:41 (+03)",
                    "Asr": "16:30 (+03)",
                    "Maghrib": "19:55 (+03)",
                    "Isha": "21:20 (+03)",
                },
            }
        ]
    }

    class _DummyResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_val, exc_tb):
            return False

        def read(self):
            return json.dumps(payload).encode("utf-8")

    monkeypatch.setattr(
        pt.urllib.request, "urlopen", lambda _req, timeout=15: _DummyResponse()
    )

    times, source = pt.get_prayer_times("Gaziantep", "Gazi̇antep", allow_network=True)

    assert source == "network"
    assert times is not None
    assert times["ogle"] == "12:41"
    cache = pt._load_cache()
    assert _cache_key("Gaziantep", "Gazi̇antep", today) in cache


def test_network_failure_enters_backoff_and_logs_once(tmp_path, monkeypatch):
    cache_path = tmp_path / "prayer_times_cache.json"
    monkeypatch.setattr(pt, "CACHE_FILE", str(cache_path))
    monkeypatch.setattr(pt, "NETWORK_RETRY_SECONDS", 3600)
    monkeypatch.setattr(pt, "_network_retry_after", {})
    monkeypatch.setattr(pt, "_last_unavailable_log_at", {})

    calls = {"weekly": 0, "aladhan": 0, "collectapi": 0}

    def _fail(name):
        def inner(_city, _district):
            calls[name] += 1
            return False

        return inner

    monkeypatch.setattr(pt, "fetch_weekly_prayer_times", _fail("weekly"))
    monkeypatch.setattr(pt, "fetch_aladhan_prayer_times", _fail("aladhan"))
    monkeypatch.setattr(pt, "fetch_collectapi_prayer_times", _fail("collectapi"))

    logged = []
    monkeypatch.setattr(pt, "_log_error_event", lambda event, data=None: logged.append((event, data)))

    missing, source = pt.get_prayer_times("Gaziantep", "Gazi̇antep", allow_network=True)
    assert missing is None
    assert source == "none"
    assert calls == {"weekly": 1, "aladhan": 1, "collectapi": 1}
    assert logged and logged[0][0] == "prayer_times_unavailable"

    missing, source = pt.get_prayer_times("Gaziantep", "Gazi̇antep", allow_network=True)
    assert missing is None
    assert source == "none"
    assert calls == {"weekly": 1, "aladhan": 1, "collectapi": 1}
    assert len(logged) == 1
