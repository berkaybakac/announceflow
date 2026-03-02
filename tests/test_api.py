#!/usr/bin/env python3
"""AnnounceFlow API Test Script - Production Readiness Check"""
import requests
import sys
import os
import json
import pytest


pytestmark = pytest.mark.integration


def _live_api_tests_enabled() -> bool:
    return os.environ.get("ANNOUNCEFLOW_RUN_LIVE_API_TESTS", "").strip() == "1"


@pytest.fixture(autouse=True)
def _skip_when_live_api_tests_disabled():
    if not _live_api_tests_enabled():
        pytest.skip(
            "Live API tests disabled. Set ANNOUNCEFLOW_RUN_LIVE_API_TESTS=1 to enable."
        )


def _load_local_config() -> dict:
    try:
        with open("config.json", "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


_LOCAL_CONFIG = _load_local_config()


def _resolve_base_url() -> str:
    candidates = []
    for raw in [
        os.environ.get("ANNOUNCEFLOW_WEB_PORT"),
        _LOCAL_CONFIG.get("web_port"),
        5001,
    ]:
        try:
            port = int(raw)
            if 1 <= port <= 65535 and port not in candidates:
                candidates.append(port)
        except (TypeError, ValueError):
            continue

    if not candidates:
        candidates = [5001]

    for port in candidates:
        url = f"http://localhost:{port}"
        try:
            r = requests.get(f"{url}/api/health", timeout=2)
            data = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
            if r.status_code == 200 and data.get("status") == "ok":
                return url
        except Exception:
            continue

    return f"http://localhost:{candidates[0]}"


def _resolve_credentials() -> tuple[str, str]:
    username = os.environ.get(
        "ANNOUNCEFLOW_TEST_USERNAME", _LOCAL_CONFIG.get("admin_username", "admin")
    )
    password = os.environ.get(
        "ANNOUNCEFLOW_TEST_PASSWORD", _LOCAL_CONFIG.get("admin_password", "admin123")
    )
    return username, password


BASE_URL = _resolve_base_url()
TEST_USERNAME, TEST_PASSWORD = _resolve_credentials()
SESSION = requests.Session()


def test_health():
    """Health endpoint calisiyor mu?"""
    r = requests.get(f"{BASE_URL}/api/health", timeout=5)
    assert r.status_code == 200, f"Status: {r.status_code}"
    data = r.json()
    assert data["status"] == "ok", f"Status: {data.get('status')}"
    print(f"  Backend: {data['player']['backend']}")
    print(f"  Volume: {data['player']['volume']}%")
    print(f"  Scheduler: {'Running' if data['scheduler']['running'] else 'Stopped'}")
    print("✓ Health check OK")


def test_login():
    """Login calisiyor mu?"""
    r = SESSION.post(
        f"{BASE_URL}/login",
        data={"username": TEST_USERNAME, "password": TEST_PASSWORD},
        allow_redirects=False,
        timeout=5,
    )
    assert r.status_code in [200, 302], f"Status: {r.status_code}"
    print("✓ Login OK")


def test_volume():
    """Ses ayari calisiyor mu?"""
    # Get current volume
    r = SESSION.get(f"{BASE_URL}/api/now-playing", timeout=5)
    current_vol = r.json().get("volume", 80)

    # Set new volume
    new_vol = 75 if current_vol != 75 else 80
    r = SESSION.post(f"{BASE_URL}/api/volume", json={"volume": new_vol}, timeout=5)
    assert r.json()["success"] == True, "Volume set failed"

    # Restore original
    SESSION.post(f"{BASE_URL}/api/volume", json={"volume": current_vol}, timeout=5)
    print(f"  Volume test: {current_vol}% -> {new_vol}% -> {current_vol}%")
    print("✓ Volume OK")


def test_player_state():
    """Player durumu alinabiliyor mu?"""
    r = SESSION.get(f"{BASE_URL}/api/now-playing", timeout=5)
    data = r.json()
    assert "volume" in data, "Missing: volume"
    assert "is_playing" in data, "Missing: is_playing"
    print(f"  Playing: {data.get('is_playing')}")
    print(f"  File: {data.get('filename') or 'None'}")
    print("✓ Player state OK")


def test_media_library():
    """Media kutuphanesi erisilebiliyor mu?"""
    r = SESSION.get(f"{BASE_URL}/api/media/music", timeout=5)
    data = r.json()
    assert "files" in data, "Missing: files"
    print(f"  Music files: {data.get('count', 0)}")
    print("✓ Media library OK")


def test_playlist_operations():
    """Playlist endpoint calisiyor mu? (Playlist Blueprint)"""
    # Test playlist/stop (en basit endpoint)
    r = SESSION.post(f"{BASE_URL}/api/playlist/stop", timeout=5)
    assert r.status_code == 200, f"Status: {r.status_code}"
    data = r.json()
    assert data.get("success") == True, "Playlist stop failed"
    print("  Playlist stop: OK")
    print("✓ Playlist blueprint OK")


def test_library_page():
    """Library sayfasi aciliyor mu? (Media Blueprint)"""
    r = SESSION.get(f"{BASE_URL}/library", timeout=5)
    assert r.status_code == 200, f"Status: {r.status_code}"
    assert (
        b"library" in r.content.lower()
        or b"k\xc3\xbct\xc3\xbcphane" in r.content.lower()
    ), "Page content check failed"
    print("  Library page rendered: OK")
    print("✓ Media blueprint (page) OK")


def test_settings_page():
    """Settings sayfasi aciliyor mu? (Settings Blueprint)"""
    r = SESSION.get(f"{BASE_URL}/settings", timeout=5)
    assert r.status_code == 200, f"Status: {r.status_code}"
    assert (
        b"settings" in r.content.lower() or b"ayarlar" in r.content.lower()
    ), "Page content check failed"
    print("  Settings page rendered: OK")
    print("✓ Settings blueprint (page) OK")


if __name__ == "__main__":
    print()
    print("=" * 40)
    print("  AnnounceFlow API Test Suite")
    print("=" * 40)
    print()

    tests = [
        ("Health Check", test_health),
        ("Login", test_login),
        ("Volume Control", test_volume),
        ("Player State", test_player_state),
        ("Media Library", test_media_library),
        ("Playlist Operations", test_playlist_operations),
        ("Library Page", test_library_page),
        ("Settings Page", test_settings_page),
    ]

    passed = 0
    failed = 0

    for name, test_func in tests:
        print(f"\n[{name}]")
        try:
            test_func()
            passed += 1
        except AssertionError as e:
            print(f"✗ FAILED: {e}")
            failed += 1
        except requests.exceptions.ConnectionError:
            print("✗ FAILED: Baglanti kurulamadi - uygulama calisiyor mu?")
            failed += 1
        except Exception as e:
            print(f"✗ FAILED: {type(e).__name__}: {e}")
            failed += 1

    print()
    print("=" * 40)
    if failed == 0:
        print(f"  SONUC: {passed}/{passed} test BASARILI ✓")
        print("  Sistem uretim icin hazir!")
    else:
        print(f"  SONUC: {passed}/{passed+failed} test basarili")
        print(f"  {failed} test BASARISIZ!")
    print("=" * 40)
    print()

    sys.exit(0 if failed == 0 else 1)
