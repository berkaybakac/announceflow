"""Boot-time playlist restore in main.main(), against a real (temp) DB.

Covers what a field Pi does after power comes back: resume the saved
playlist from the saved track, or defer it while a silence policy
(working hours / prayer) is active.
"""
from __future__ import annotations

import sys
import types
from unittest.mock import MagicMock

import pytest

import database as db
import main as main_mod


class _StopMainLoop(Exception):
    pass


def _run_main_boot(monkeypatch, silence_policy=None):
    """Run main() up to the web server start with a fake player/scheduler."""
    policy = silence_policy or {"silence_active": False, "policy": None}
    log_system = MagicMock()
    log_error = MagicMock()
    player = MagicMock()
    scheduler = MagicMock()

    monkeypatch.setattr(main_mod, "setup_logging", lambda: MagicMock())
    monkeypatch.setattr(
        main_mod,
        "_load_release_stamp",
        lambda *_a, **_k: {"ref": "t", "commit_short": "t", "branch": "t", "deployed_at_utc": "t"},
    )
    monkeypatch.setattr(main_mod, "log_system", log_system)
    monkeypatch.setattr(main_mod, "log_error", log_error)
    monkeypatch.setattr(main_mod, "get_player", lambda: player)
    monkeypatch.setattr(main_mod, "get_scheduler", lambda: scheduler)
    monkeypatch.setattr(main_mod, "load_config", lambda: {"web_port": 5001})
    monkeypatch.setattr(main_mod, "resolve_silence_policy", lambda *_a, **_k: dict(policy))
    monkeypatch.setattr(main_mod, "_is_port_available", lambda _port: True)
    monkeypatch.setattr(main_mod.time, "sleep", lambda _x: None)
    monkeypatch.setattr(main_mod.signal, "signal", lambda *_a: None)

    monkeypatch.setenv("ANNOUNCEFLOW_DEV_RELOAD", "1")
    fake_app = MagicMock()
    fake_app.run.side_effect = _StopMainLoop()
    fake_web_panel = types.ModuleType("web_panel")
    fake_web_panel.app = fake_app
    monkeypatch.setitem(sys.modules, "web_panel", fake_web_panel)

    with pytest.raises(_StopMainLoop):
        main_mod.main()
    return player, scheduler, log_system, log_error


@pytest.fixture
def tracks(tmp_path):
    paths = []
    for name in ("a.mp3", "b.mp3", "c.mp3"):
        p = tmp_path / name
        p.write_bytes(b"")
        paths.append(str(p))
    return paths


def test_resumes_saved_playlist_from_saved_track(monkeypatch, temp_db, tracks):
    db.save_playlist_state(playlist=tracks, index=2, loop=True, active=True)

    player, scheduler, log_system, _ = _run_main_boot(monkeypatch)

    # index - 1 because play_next() advances before playing.
    player.apply_playlist_state.assert_called_once_with(
        playlist=tracks, index=1, loop=True, runtime_active=True
    )
    player.play_next.assert_called_once_with()
    scheduler.defer_playlist_restore.assert_not_called()
    log_system.assert_any_call("playlist_restore", {"tracks": 3, "index": 2})


def test_missing_files_are_dropped_and_out_of_range_index_resets(monkeypatch, temp_db, tracks):
    db.save_playlist_state(playlist=tracks, index=2, loop=False, active=True)
    import os
    os.remove(tracks[1])

    player, _, _, _ = _run_main_boot(monkeypatch)

    player.apply_playlist_state.assert_called_once_with(
        playlist=[tracks[0], tracks[2]], index=-1, loop=False, runtime_active=True
    )
    player.play_next.assert_called_once_with()


@pytest.mark.parametrize("policy_name", ["working_hours", "prayer"])
def test_restore_is_deferred_while_silence_policy_active(monkeypatch, temp_db, tracks, policy_name):
    db.save_playlist_state(playlist=tracks, index=1, loop=True, active=True)

    player, scheduler, log_system, _ = _run_main_boot(
        monkeypatch, {"silence_active": True, "policy": policy_name}
    )

    player.apply_playlist_state.assert_called_once_with(
        playlist=tracks, index=0, loop=True, runtime_active=False
    )
    player.play_next.assert_not_called()
    scheduler.defer_playlist_restore.assert_called_once_with(
        policy_name, {"playlist": tracks, "index": 1, "loop": True, "active": True}
    )
    assert any(c.args[0] == "playlist_restore_deferred" for c in log_system.call_args_list)


def test_all_files_missing_logs_failure_and_does_not_play(monkeypatch, temp_db, tmp_path):
    db.save_playlist_state(playlist=[str(tmp_path / "gone.mp3")], index=0, loop=True, active=True)

    player, _, _, log_error = _run_main_boot(monkeypatch)

    player.apply_playlist_state.assert_not_called()
    player.play_next.assert_not_called()
    log_error.assert_any_call("playlist_restore_failed", {"reason": "files_not_found"})


def test_inactive_playlist_is_not_restored(monkeypatch, temp_db, tracks):
    db.save_playlist_state(playlist=tracks, index=0, loop=True, active=False)

    player, scheduler, _, _ = _run_main_boot(monkeypatch)

    player.apply_playlist_state.assert_not_called()
    player.play_next.assert_not_called()
    scheduler.defer_playlist_restore.assert_not_called()
