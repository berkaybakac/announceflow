"""Prayer cache horizon and resilience tests."""

from __future__ import annotations

import json
from datetime import datetime, timedelta

import prayer_times as pt


def _cache_key(city: str, district: str, date_obj: datetime) -> str:
    return f"{city}_{district}_{date_obj.strftime('%Y-%m-%d')}"


def test_fetch_prunes_to_30_days(tmp_path, monkeypatch):
    cache_path = tmp_path / "prayer_times_cache.json"
    monkeypatch.setattr(pt, "CACHE_FILE", str(cache_path))
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

    stale_out_of_horizon = now - timedelta(days=pt.CACHE_HORIZON_DAYS + 2)
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
