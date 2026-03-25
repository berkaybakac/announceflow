"""Unit tests for main.py graceful shutdown signal handler."""

from __future__ import annotations

import signal as signal_mod
import sys
import types
from unittest.mock import MagicMock

import pytest

import main as main_mod


class _StopMainLoop(Exception):
    """Raised by fake app.run to stop main() during tests."""


def _bootstrap_main(monkeypatch):
    """Run main() until app.run, then return captured signal handlers and mocks."""
    logger = MagicMock()
    log_system_mock = MagicMock()

    monkeypatch.setattr(main_mod, "setup_logging", lambda: logger)
    monkeypatch.setattr(
        main_mod,
        "_load_release_stamp",
        lambda *_a, **_k: {
            "ref": "test-ref",
            "commit_short": "deadbee",
            "branch": "test",
            "deployed_at_utc": "2026-03-16T00:00:00Z",
        },
    )
    monkeypatch.setattr(main_mod, "log_system", log_system_mock)
    monkeypatch.setattr(main_mod.db, "init_database", MagicMock())
    monkeypatch.setattr(main_mod.db, "get_volume_state", MagicMock(return_value={"volume": 43}))
    monkeypatch.setattr(
        main_mod.db,
        "get_playlist_state",
        MagicMock(return_value={"active": False, "playlist": []}),
    )

    player = MagicMock()
    scheduler = MagicMock()
    monkeypatch.setattr(main_mod, "get_player", lambda: player)
    monkeypatch.setattr(main_mod, "get_scheduler", lambda: scheduler)
    monkeypatch.setattr(main_mod, "load_config", lambda: {"web_port": 5001})
    monkeypatch.setattr(main_mod, "_is_port_available", lambda _port: True)
    monkeypatch.setattr(main_mod.time, "sleep", lambda _x: None)

    monkeypatch.setenv("ANNOUNCEFLOW_DEV_RELOAD", "1")
    fake_app = MagicMock()
    fake_app.run.side_effect = _StopMainLoop()
    fake_web_panel = types.ModuleType("web_panel")
    fake_web_panel.app = fake_app
    monkeypatch.setitem(sys.modules, "web_panel", fake_web_panel)

    registered = {}

    def _fake_signal(sig, handler):
        registered[sig] = handler

    monkeypatch.setattr(main_mod.signal, "signal", _fake_signal)

    with pytest.raises(_StopMainLoop):
        main_mod.main()

    return {
        "logger": logger,
        "log_system": log_system_mock,
        "player": player,
        "scheduler": scheduler,
        "registered": registered,
    }


def test_registers_sigint_and_sigterm_handlers(monkeypatch):
    state = _bootstrap_main(monkeypatch)
    assert signal_mod.SIGINT in state["registered"]
    assert signal_mod.SIGTERM in state["registered"]
    assert callable(state["registered"][signal_mod.SIGINT])
    assert callable(state["registered"][signal_mod.SIGTERM])


def test_graceful_exit_stops_stream_scheduler_and_player(monkeypatch):
    state = _bootstrap_main(monkeypatch)

    stream_service = MagicMock()
    get_stream_service = MagicMock(return_value=stream_service)
    fake_stream_mod = types.ModuleType("services.stream_service")
    fake_stream_mod.get_stream_service = get_stream_service
    monkeypatch.setitem(sys.modules, "services.stream_service", fake_stream_mod)

    exit_mock = MagicMock(side_effect=SystemExit(0))
    monkeypatch.setattr(main_mod.sys, "exit", exit_mock)

    handler = state["registered"][signal_mod.SIGTERM]
    with pytest.raises(SystemExit):
        handler(signal_mod.SIGTERM, None)

    get_stream_service.assert_called_once_with()
    stream_service.stop.assert_called_once_with()
    state["scheduler"].stop.assert_called_once_with()
    state["player"].stop.assert_called_once_with()
    state["log_system"].assert_any_call("shutdown", {"signal": "SIGTERM"})
    exit_mock.assert_called_once_with(0)


def test_graceful_exit_continues_when_stream_stop_raises(monkeypatch):
    state = _bootstrap_main(monkeypatch)

    stream_service = MagicMock()
    stream_service.stop.side_effect = RuntimeError("stream stop failed")
    get_stream_service = MagicMock(return_value=stream_service)
    fake_stream_mod = types.ModuleType("services.stream_service")
    fake_stream_mod.get_stream_service = get_stream_service
    monkeypatch.setitem(sys.modules, "services.stream_service", fake_stream_mod)

    exit_mock = MagicMock(side_effect=SystemExit(0))
    monkeypatch.setattr(main_mod.sys, "exit", exit_mock)

    handler = state["registered"][signal_mod.SIGINT]
    with pytest.raises(SystemExit):
        handler(signal_mod.SIGINT, None)

    # Shutdown flow should continue even if stream stop raises.
    state["scheduler"].stop.assert_called_once_with()
    state["player"].stop.assert_called_once_with()
    state["log_system"].assert_any_call("shutdown", {"signal": "SIGINT"})
    exit_mock.assert_called_once_with(0)
    assert any(
        call.args and "WebStream: shutdown error:" in str(call.args[0])
        for call in state["logger"].debug.call_args_list
    )
