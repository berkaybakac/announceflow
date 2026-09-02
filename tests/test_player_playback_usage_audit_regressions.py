"""Regression tests for playback_usage_audit on manual stop paths."""

from __future__ import annotations

import time
from unittest.mock import patch

from player import AudioPlayer


def _make_player() -> AudioPlayer:
    with patch("player.AudioPlayer._build_alsa_device_candidates", return_value=[]), patch(
        "player.AudioPlayer._build_alsa_card_candidates", return_value=[]
    ):
        return AudioPlayer()


def test_stop_emits_playback_usage_audit_happy_path(monkeypatch):
    """Happy path: stop() should emit a stopped audit entry for active playback."""
    player = _make_player()
    calls = []

    monkeypatch.setattr("player.db.save_playlist_state", lambda *args, **kwargs: None)
    monkeypatch.setattr("player.log_play", lambda event, data: calls.append((event, data)))

    player.is_playing = True
    player.current_file = "/tmp/song_a.mp3"
    player._session_play_started_at = time.monotonic() - 3.0
    player._process = None

    assert player.stop() is True

    audits = [data for event, data in calls if event == "playback_usage_audit"]
    assert len(audits) == 1
    assert audits[0]["status"] == "stopped"
    assert audits[0]["file"] == "song_a.mp3"
    assert audits[0]["duration_seconds"] >= 0.0


def test_stop_playback_only_emits_interrupted_branch(monkeypatch):
    """Branch path: _stop_playback_only should emit interrupted audit."""
    player = _make_player()
    calls = []

    monkeypatch.setattr("player.log_play", lambda event, data: calls.append((event, data)))

    player.is_playing = True
    player.current_file = "/tmp/song_b.mp3"
    player._session_play_started_at = time.monotonic() - 2.0
    player._process = None

    player._stop_playback_only()

    audits = [data for event, data in calls if event == "playback_usage_audit"]
    assert len(audits) == 1
    assert audits[0]["status"] == "interrupted"
    assert audits[0]["file"] == "song_b.mp3"
    assert audits[0]["duration_seconds"] >= 0.0


def test_stop_without_active_playback_sad_path_no_audit(monkeypatch):
    """Sad path: no active playback should not emit playback_usage_audit."""
    player = _make_player()
    calls = []

    monkeypatch.setattr("player.db.save_playlist_state", lambda *args, **kwargs: None)
    monkeypatch.setattr("player.log_play", lambda event, data: calls.append((event, data)))

    player.is_playing = False
    player.current_file = "/tmp/song_c.mp3"
    player._session_play_started_at = 0.0
    player._process = None

    assert player.stop() is True

    audits = [data for event, data in calls if event == "playback_usage_audit"]
    assert audits == []



def test_stop_persists_music_usage_session(monkeypatch):
    """stop() should also persist the duration to usage_sessions (kind=music)."""
    player = _make_player()
    calls = []

    monkeypatch.setattr("player.db.save_playlist_state", lambda *args, **kwargs: None)
    monkeypatch.setattr("player.log_play", lambda event, data: None)
    monkeypatch.setattr(
        "player.db.record_usage_session", lambda **kwargs: calls.append(kwargs)
    )

    player.is_playing = True
    player.current_file = "/tmp/song_c.mp3"
    player._session_play_started_at = time.monotonic() - 5.0
    player._process = None

    player.stop()

    assert len(calls) == 1
    assert calls[0]["kind"] == "music"
    assert calls[0]["source"] == "song_c.mp3"
    assert calls[0]["duration_seconds"] >= 5.0
    assert calls[0]["started_at"] < calls[0]["ended_at"]


def test_stop_playback_only_persists_music_usage_session(monkeypatch):
    """_stop_playback_only should also persist the duration (interrupted branch)."""
    player = _make_player()

    monkeypatch.setattr("player.log_play", lambda event, data: None)
    calls = []
    monkeypatch.setattr(
        "player.db.record_usage_session", lambda **kwargs: calls.append(kwargs)
    )

    player.is_playing = True
    player.current_file = "/tmp/song_d.mp3"
    player._session_play_started_at = time.monotonic() - 4.0
    player._process = None

    player._stop_playback_only()

    assert len(calls) == 1
    assert calls[0]["kind"] == "music"
    assert calls[0]["source"] == "song_d.mp3"
