"""Regression matrix for diagnose auth and metric branch coverage."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest
from flask import Flask

import diagnose
from routes.player_routes import player_bp


def _iso_utc(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds").replace("+00:00", "Z")


@pytest.fixture
def app():
    app = Flask(__name__)
    app.secret_key = "test-secret"
    app.register_blueprint(player_bp)
    return app


@pytest.fixture
def anon_client(app):
    return app.test_client()


@pytest.fixture
def authed_client(app):
    client = app.test_client()
    with client.session_transaction() as sess:
        sess["logged_in"] = True
    return client


def test_diagnose_requires_login_sad_path(anon_client):
    """Sad path: diagnose endpoint must not be publicly accessible."""
    resp = anon_client.get("/api/diagnose", follow_redirects=False)
    assert resp.status_code in (301, 302)
    assert "/login" in (resp.headers.get("Location") or "")


def test_diagnose_metric_branches_happy_critical(authed_client, tmp_path, monkeypatch):
    """Happy/branch: skipped-track aggregation and Wi-Fi filtering stay correct."""
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    log_file = log_dir / "events.jsonl"

    now = datetime.now(timezone.utc)
    t0 = now - timedelta(minutes=5)

    entries = [
        {"ts": _iso_utc(t0), "event": "system_health", "data": {"wifi_signal_dbm": -1, "temp_c": 54.0, "load_1m": 0.4}},
        {"ts": _iso_utc(t0 + timedelta(seconds=10)), "event": "system_health", "data": {"wifi_signal_dbm": -67, "temp_c": 55.0, "load_1m": 0.5}},
        {"ts": _iso_utc(t0 + timedelta(seconds=20)), "event": "system_health", "data": {"wifi_signal_dbm": 7, "temp_c": 56.0, "load_1m": 0.6}},
        {"ts": _iso_utc(t0 + timedelta(seconds=30)), "event": "playback_usage_audit", "data": {"status": "interrupted"}},
        {"ts": _iso_utc(t0 + timedelta(seconds=40)), "event": "playback_usage_audit", "data": {"status": "stopped"}},
        {"ts": _iso_utc(t0 + timedelta(seconds=50)), "event": "playback_usage_audit", "data": {"status": "completed"}},
        {"ts": _iso_utc(t0 + timedelta(seconds=60)), "event": "playlist_track_missing", "data": {}},
        {"ts": _iso_utc(t0 + timedelta(seconds=70)), "event": "playlist_track_start_failed", "data": {}},
        {"ts": _iso_utc(t0 + timedelta(seconds=80)), "event": "tracks_skipped", "data": {}},
        {"ts": _iso_utc(t0 + timedelta(seconds=90)), "event": "track_end", "data": {}},
    ]

    with open(log_file, "w", encoding="utf-8") as f:
        for entry in entries:
            f.write(json.dumps(entry) + "\n")

    monkeypatch.setattr(diagnose, "LOG_FILE", str(log_file))

    resp = authed_client.get("/api/diagnose?minutes=120")
    assert resp.status_code == 200
    payload = resp.get_json()

    # interrupted + stopped + track_missing + start_failed + tracks_skipped
    assert payload["tracks_skipped"] == 5
    assert payload["tracks_played"] == 1
    assert payload["wifi_signals"] == [-67]

