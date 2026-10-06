"""Prayer-time silence: window edges, and the pause → resume choreography.

The pause/resume part uses a real AudioPlayer and a real temp DB; only
`AudioPlayer.play` (the mpg123 subprocess boundary) is faked.
"""
from __future__ import annotations

from datetime import datetime

import pytest

import database as db
from player import AudioPlayer
from scheduler import Scheduler
from services.silence_policy import (
    _is_prayer_window_active,
    is_within_working_hours,
    resolve_silence_policy,
)

TIMES = {"imsak": "05:10", "ogle": "13:05", "ikindi": "16:20", "aksam": "18:45", "yatsi": "20:05"}


def _at(hhmm: str) -> datetime:
    h, m = map(int, hhmm.split(":"))
    return datetime(2026, 10, 5, h, m)


# ── Window edges ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "now, active",
    [
        ("13:03", False),
        ("13:04", True),   # prayer - 1 min buffer
        ("13:05", True),
        ("13:11", True),   # prayer + 5 + 1 min buffer
        ("13:12", False),
        ("12:00", False),
    ],
)
def test_prayer_window_edges(now, active):
    assert _is_prayer_window_active(TIMES, buffer_minutes=1, now=_at(now)) is active


@pytest.mark.parametrize(
    "start, end, now, inside",
    [
        ("09:00", "22:00", "08:59", False),
        ("09:00", "22:00", "09:00", True),
        ("09:00", "22:00", "22:00", True),
        ("09:00", "22:00", "22:01", False),
        ("22:00", "06:00", "23:30", True),   # overnight window
        ("22:00", "06:00", "05:59", True),
        ("22:00", "06:00", "12:00", False),
    ],
)
def test_working_hours_window(start, end, now, inside):
    config = {"working_hours_enabled": True, "working_hours_start": start, "working_hours_end": end}
    assert is_within_working_hours(config, now=_at(now)) is inside


def test_working_hours_disabled_means_always_inside():
    assert is_within_working_hours({"working_hours_enabled": False}, now=_at("03:00")) is True


def test_outside_working_hours_takes_precedence_over_prayer():
    config = {
        "working_hours_enabled": True,
        "working_hours_start": "14:00",
        "working_hours_end": "22:00",
        "prayer_times_enabled": True,
        "prayer_times_city": "Osmaniye",
    }
    decision = resolve_silence_policy(
        config, allow_network=False, fail_safe_on_unknown=False, now=_at("13:05"),
        prayer_times_provider=lambda *_: (TIMES, "cache"),
    )
    assert decision["policy"] == "working_hours"
    assert decision["silence_active"] is True


def test_prayer_window_resolves_to_prayer_silence():
    config = {"prayer_times_enabled": True, "prayer_times_city": "Osmaniye"}
    decision = resolve_silence_policy(
        config, allow_network=False, fail_safe_on_unknown=False, now=_at("13:06"),
        prayer_times_provider=lambda *_: (TIMES, "cache"),
    )
    assert (decision["policy"], decision["silence_active"]) == ("prayer", True)


# ── Pause → resume with a real player ─────────────────────────────────────────

PRAYER = {"silence_active": True, "policy": "prayer"}
CLEAR = {"silence_active": False, "policy": "none"}
CONFIG = {"working_hours_enabled": False}


@pytest.fixture
def player(monkeypatch, temp_db, tmp_path):
    played = []

    def fake_play(self, file_path, *args, **kwargs):
        played.append(file_path)
        self.is_playing = True
        self.current_file = file_path
        return True

    monkeypatch.setattr(AudioPlayer, "play", fake_play)
    p = AudioPlayer()
    p.played = played
    return p


@pytest.fixture
def tracks(tmp_path):
    paths = []
    for name in ("a.mp3", "b.mp3", "c.mp3"):
        f = tmp_path / name
        f.write_bytes(b"")
        paths.append(str(f))
    return paths


def _start_playlist_on_second_track(player, tracks):
    player.apply_playlist_state(playlist=tracks, index=-1, loop=True,
                                runtime_active=True, db_active=True)
    player.play_next()  # a
    player.play_next()  # b (index 1)
    assert player.current_file == tracks[1]


def test_prayer_pauses_then_resumes_with_next_track(player, tracks):
    _start_playlist_on_second_track(player, tracks)
    sched = Scheduler()

    assert sched._handle_prayer_time(CONFIG, player, PRAYER) is True
    assert player.is_playing is False
    assert player._playlist_active is False
    assert db.get_playlist_state()["active"] is True, "resume intent must survive the pause"

    # Repeated ticks inside the window keep it silent and don't re-save state.
    assert sched._handle_prayer_time(CONFIG, player, PRAYER) is True
    assert player.is_playing is False

    played_before = len(player.played)
    assert sched._handle_prayer_time(CONFIG, player, CLEAR) is False

    # Current behaviour: the interrupted track (b) is not replayed; playback
    # continues with the following track (c).
    assert player.played[played_before:] == [tracks[2]]
    assert player.is_playing is True
    assert db.get_playlist_state()["active"] is True
    assert db.get_playlist_state()["index"] == 2


def test_manual_stop_during_prayer_cancels_resume(player, tracks):
    _start_playlist_on_second_track(player, tracks)
    sched = Scheduler()
    sched._handle_prayer_time(CONFIG, player, PRAYER)

    player.stop_playlist()  # operator presses stop during ezan
    played_before = len(player.played)
    sched._handle_prayer_time(CONFIG, player, CLEAR)

    assert player.played[played_before:] == []
    assert db.get_playlist_state()["active"] is False


def test_prayer_with_nothing_playing_does_not_start_music_afterwards(player):
    sched = Scheduler()
    sched._handle_prayer_time(CONFIG, player, PRAYER)
    sched._handle_prayer_time(CONFIG, player, CLEAR)
    assert player.played == []
