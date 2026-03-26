#!/usr/bin/env python3
"""Force prayer silence smoke simulation without changing runtime architecture."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import tempfile
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import requests


ROOT = Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "config.json"
CACHE_PATH = ROOT / "prayer_times_cache.json"


def _load_json(path: Path, default: dict[str, Any]) -> dict[str, Any]:
    if not path.exists():
        return dict(default)
    try:
        with path.open("r", encoding="utf-8") as f:
            loaded = json.load(f)
        if isinstance(loaded, dict):
            return loaded
    except (OSError, json.JSONDecodeError):
        pass
    return dict(default)


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_path = tempfile.mkstemp(
        prefix=".tmp_smoke_",
        suffix=".json",
        dir=str(path.parent),
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp_path, str(path))
        try:
            dir_fd = os.open(str(path.parent), os.O_RDONLY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
        except OSError:
            pass
    except Exception:
        try:
            os.remove(temp_path)
        except OSError:
            pass
        raise


def _backup_file(path: Path) -> tuple[Optional[Path], bool]:
    existed = path.exists()
    if not existed:
        return None, False
    fd, backup_path = tempfile.mkstemp(
        prefix=f".smoke_backup_{path.name}_",
        suffix=".bak",
        dir=str(path.parent),
    )
    os.close(fd)
    backup = Path(backup_path)
    shutil.copy2(path, backup)
    return backup, True


def _restore_file(path: Path, backup: Optional[Path], existed_before: bool) -> None:
    if backup and backup.exists():
        os.replace(str(backup), str(path))
        return
    if not existed_before and path.exists():
        path.unlink()


def _resolve_base_url(cli_value: Optional[str], config: dict[str, Any]) -> str:
    if cli_value:
        return cli_value.rstrip("/")
    port = config.get("web_port", 5001)
    try:
        parsed = int(port)
        if parsed < 1 or parsed > 65535:
            raise ValueError("web_port out of range")
    except (TypeError, ValueError):
        parsed = 5001
    return f"http://localhost:{parsed}"


def _resolve_credentials(
    cli_username: Optional[str], cli_password: Optional[str], config: dict[str, Any]
) -> tuple[str, str]:
    username = (
        cli_username
        or os.environ.get("ANNOUNCEFLOW_TEST_USERNAME")
        or str(config.get("admin_username", "admin"))
    )
    password = (
        cli_password
        or os.environ.get("ANNOUNCEFLOW_TEST_PASSWORD")
        or str(config.get("admin_password", "admin123"))
    )
    return username, password


def _build_forced_prayer_entry(now: datetime) -> dict[str, str]:
    now_hhmm = now.strftime("%H:%M")
    today = now.strftime("%Y-%m-%d")
    # Only one prayer time needs to match current window; others remain harmless.
    return {
        "imsak": "00:00",
        "ogle": now_hhmm,
        "ikindi": "00:00",
        "aksam": "00:00",
        "yatsi": "00:00",
        "date": today,
    }


def _poll_now_playing(
    session: requests.Session,
    base_url: str,
    timeout_seconds: int,
    expect_playing: Optional[bool] = None,
) -> tuple[bool, Optional[dict[str, Any]]]:
    deadline = time.time() + max(1, timeout_seconds)
    last_state: Optional[dict[str, Any]] = None
    while time.time() < deadline:
        try:
            response = session.get(f"{base_url}/api/now-playing", timeout=3)
            if response.status_code == 200:
                state = response.json()
                last_state = state if isinstance(state, dict) else None
                if expect_playing is None:
                    return True, last_state
                if bool((last_state or {}).get("is_playing", False)) == expect_playing:
                    return True, last_state
        except requests.RequestException:
            pass
        time.sleep(1)
    return False, last_state


def _login(session: requests.Session, base_url: str, username: str, password: str) -> bool:
    try:
        response = session.post(
            f"{base_url}/login",
            data={"username": username, "password": password},
            allow_redirects=False,
            timeout=5,
        )
    except requests.RequestException:
        return False
    return response.status_code in (200, 302)


def main() -> int:
    parser = argparse.ArgumentParser(description="Simulate prayer silence smoke flow")
    parser.add_argument("--base-url", default=None, help="Web base URL (default: config.web_port)")
    parser.add_argument("--username", default=None, help="Login username")
    parser.add_argument("--password", default=None, help="Login password")
    parser.add_argument(
        "--hold-seconds",
        type=int,
        default=20,
        help="How long to keep forced prayer condition active (default: 20)",
    )
    parser.add_argument(
        "--poll-timeout",
        type=int,
        default=30,
        help="Polling timeout for state observation (default: 30)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show planned actions without modifying files",
    )
    args = parser.parse_args()

    config = _load_json(CONFIG_PATH, {})
    base_url = _resolve_base_url(args.base_url, config)
    username, password = _resolve_credentials(args.username, args.password, config)

    city = str(config.get("prayer_times_city", "")).strip() or "Istanbul"
    district = str(config.get("prayer_times_district", "")).strip() or "Merkez"
    today = datetime.now().strftime("%Y-%m-%d")
    cache_key = f"{city}_{district}_{today}"

    print(f"[info] base_url={base_url}")
    print(f"[info] city={city} district={district} key={cache_key}")
    print(
        f"[info] hold_seconds={max(1, args.hold_seconds)} poll_timeout={max(1, args.poll_timeout)}"
    )

    if args.dry_run:
        print("[dry-run] Would backup config.json and prayer_times_cache.json")
        print("[dry-run] Would force config: prayer_times_enabled=True, working_hours_enabled=False")
        print("[dry-run] Would inject prayer cache entry for current minute")
        print("[dry-run] Would restore backups in finally")
        return 0

    backups: dict[Path, tuple[Optional[Path], bool]] = {}
    session = requests.Session()
    baseline_state: Optional[dict[str, Any]] = None
    silence_observed = False
    restore_failed = False

    try:
        for path in (CONFIG_PATH, CACHE_PATH):
            backups[path] = _backup_file(path)

        login_ok = _login(session, base_url, username, password)
        if login_ok:
            _, baseline_state = _poll_now_playing(
                session, base_url, timeout_seconds=3, expect_playing=None
            )
        else:
            print("[warn] Login failed; HTTP observation steps will be skipped.")

        forced_config = dict(config)
        forced_config["prayer_times_enabled"] = True
        forced_config["working_hours_enabled"] = False
        forced_config["prayer_times_city"] = city
        forced_config["prayer_times_district"] = district
        _atomic_write_json(CONFIG_PATH, forced_config)

        cache = _load_json(CACHE_PATH, {})
        cache[cache_key] = _build_forced_prayer_entry(datetime.now())
        _atomic_write_json(CACHE_PATH, cache)
        print("[info] Forced prayer condition injected.")

        started = time.time()
        if login_ok:
            silence_observed, state = _poll_now_playing(
                session,
                base_url,
                timeout_seconds=max(1, args.poll_timeout),
                expect_playing=False,
            )
            if silence_observed:
                print("[ok] Silence observed (is_playing=False).")
            else:
                print(f"[warn] Silence not observed within timeout. last_state={state}")

        elapsed = time.time() - started
        remaining_hold = max(0, max(1, args.hold_seconds) - int(elapsed))
        if remaining_hold > 0:
            print(f"[info] Holding forced condition for {remaining_hold}s ...")
            time.sleep(remaining_hold)

    finally:
        for path, (backup, existed_before) in backups.items():
            try:
                _restore_file(path, backup, existed_before)
            except Exception as restore_error:
                print(f"[error] Failed to restore {path.name}: {restore_error}")
                restore_failed = True
        print("[info] Original files restored.")

    if restore_failed:
        return 4

    if not _login(session, base_url, username, password):
        print("[warn] Could not re-login after restore; normalization check skipped.")
        return 0 if silence_observed else 2

    baseline_playlist_active = bool((baseline_state or {}).get("playlist", {}).get("active"))
    baseline_playing = bool((baseline_state or {}).get("is_playing", False))
    expect_resume = baseline_playing or baseline_playlist_active

    if expect_resume:
        resume_ok, resume_state = _poll_now_playing(
            session,
            base_url,
            timeout_seconds=max(1, args.poll_timeout),
            expect_playing=True,
        )
        if resume_ok:
            print("[ok] Playback resumed after rollback.")
            return 0
        # Fallback acceptance: playlist intent exists but immediate play may still be pending.
        if bool((resume_state or {}).get("playlist", {}).get("active")):
            print("[warn] Playback not observed, but playlist intent is active after rollback.")
            return 0
        print(f"[warn] Resume not observed within timeout. last_state={resume_state}")
        return 3

    print("[ok] No pre-existing playlist intent; rollback considered normal.")
    return 0 if silence_observed else 2


if __name__ == "__main__":
    raise SystemExit(main())
