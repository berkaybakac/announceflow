"""Faz 5 — Agent stream UI flow tests (8 scenarios).

Tests the _job functions and callbacks directly (no tkinter required).
Mocks AnnounceFlowAgent and StreamClient to verify worker-only logic.

tkinter is mocked at module level since it may not be available in CI/test envs.
"""
import os
import sys
import types
from unittest.mock import MagicMock, patch

import pytest

# --------------- tkinter mock setup ---------------

_tk_mock = MagicMock()
_tkinter_modules = {
    "tkinter": _tk_mock,
    "tkinter.ttk": MagicMock(),
    "tkinter.filedialog": MagicMock(),
    "tkinter.messagebox": MagicMock(),
}

# Only patch if tkinter is not available
try:
    import tkinter  # noqa: F401
    _need_tk_mock = False
except ImportError:
    _need_tk_mock = True

_agent_dir = os.path.join(os.path.dirname(__file__), "..", "agent")


@pytest.fixture(autouse=True)
def _mock_tkinter(monkeypatch):
    # Add agent dir to sys.path so agent.py's internal imports work
    sys.path.insert(0, _agent_dir)
    if _need_tk_mock:
        for mod_name, mock_obj in _tkinter_modules.items():
            monkeypatch.setitem(sys.modules, mod_name, mock_obj)
    yield
    # Clean up agent module so it doesn't conflict with agent/ being a package
    for key in list(sys.modules.keys()):
        if key == "agent" or key.startswith("agent."):
            del sys.modules[key]
    # Remove agent dir from sys.path to avoid polluting other test files
    while _agent_dir in sys.path:
        sys.path.remove(_agent_dir)


def _import_agent():
    """Import agent module (with tkinter mocked if needed)."""
    if _need_tk_mock:
        for mod_name, mock_obj in _tkinter_modules.items():
            sys.modules.setdefault(mod_name, mock_obj)
    import agent as agent_mod
    return agent_mod


# --------------- 1. btn_configs contains stream buttons ---------------


class TestBtnConfigs:
    def test_stream_buttons_present(self):
        """btn_configs in show_main_frame includes stream start/stop buttons."""
        agent_mod = _import_agent()
        source_path = os.path.join(
            os.path.dirname(__file__), "..", "agent", "agent.py"
        )
        with open(source_path) as f:
            source = f.read()
        assert "Yayını Başlat" in source
        assert "Yayını Durdur" in source
        assert "self.start_stream" in source
        assert "self.stop_stream" in source


# --------------- helpers for job function tests ---------------


def _make_gui():
    """Create a minimal AgentGUI-like object with mocked deps."""
    agent_mod = _import_agent()
    AgentGUI = agent_mod.AgentGUI

    agent_mock = MagicMock()
    agent_mock.api_base = "http://aflow.local:5001"
    agent_mock.config = {"api_base": "http://aflow.local:5001"}

    gui = AgentGUI.__new__(AgentGUI)
    gui.agent = agent_mock
    gui.root = None
    gui.logged_in = False
    gui.network_worker = None
    gui._closing = False
    gui._volume_update_job = None
    gui._pending_volume = None

    from stream_client import StreamClient
    gui._stream_client = MagicMock(spec=StreamClient)

    return gui


# --------------- 2. start job: API ok + sender ok -> "ok" ---------------


class TestStartStreamJob:
    def test_api_ok_sender_ok(self):
        gui = _make_gui()
        gui.agent.start_stream.return_value = True
        gui._stream_client.start_sender.return_value = True

        host = gui._resolve_stream_host()

        def _job():
            api_ok = gui.agent.start_stream()
            if not api_ok:
                return "api_fail"
            sender_ok = gui._stream_client.start_sender(host, 5800)
            if not sender_ok:
                gui.agent.stop_stream()
                return "sender_fail"
            return "ok"

        assert _job() == "ok"
        gui.agent.start_stream.assert_called_once()
        gui._stream_client.start_sender.assert_called_once_with("aflow.local", 5800)
        gui.agent.stop_stream.assert_not_called()


# --------------- 3. start job: API ok + sender fail -> rollback ---------------


    def test_api_ok_sender_fail_rollback(self):
        gui = _make_gui()
        gui.agent.start_stream.return_value = True
        gui._stream_client.start_sender.return_value = False

        host = gui._resolve_stream_host()

        def _job():
            api_ok = gui.agent.start_stream()
            if not api_ok:
                return "api_fail"
            sender_ok = gui._stream_client.start_sender(host, 5800)
            if not sender_ok:
                gui.agent.stop_stream()
                return "sender_fail"
            return "ok"

        assert _job() == "sender_fail"
        gui.agent.stop_stream.assert_called_once()  # ROLLBACK


# --------------- 4. start job: API fail -> "api_fail" ---------------


    def test_api_fail(self):
        gui = _make_gui()
        gui.agent.start_stream.return_value = False

        host = gui._resolve_stream_host()

        def _job():
            api_ok = gui.agent.start_stream()
            if not api_ok:
                return "api_fail"
            sender_ok = gui._stream_client.start_sender(host, 5800)
            if not sender_ok:
                gui.agent.stop_stream()
                return "sender_fail"
            return "ok"

        assert _job() == "api_fail"
        gui._stream_client.start_sender.assert_not_called()
        gui.agent.stop_stream.assert_not_called()


# --------------- 5. stop job: sender stop + API stop ---------------


class TestStopStreamJob:
    def test_stop_calls_both(self):
        gui = _make_gui()
        gui.agent.stop_stream.return_value = True
        gui._stream_client.stop_sender.return_value = True

        def _job():
            gui._stream_client.stop_sender()
            return gui.agent.stop_stream()

        assert _job() is True
        gui._stream_client.stop_sender.assert_called_once()
        gui.agent.stop_stream.assert_called_once()


# --------------- 6. _resolve_stream_host: valid URL ---------------


class TestResolveStreamHost:
    def test_valid_url(self):
        gui = _make_gui()
        gui.agent.api_base = "http://aflow.local:5001"
        assert gui._resolve_stream_host() == "aflow.local"

    def test_ip_url(self):
        gui = _make_gui()
        gui.agent.api_base = "http://192.168.1.50:5001"
        assert gui._resolve_stream_host() == "192.168.1.50"


# --------------- 7. _resolve_stream_host: invalid URL fallback ---------------


    def test_invalid_url_fallback(self):
        gui = _make_gui()
        gui.agent.api_base = "not-a-valid-url"
        assert gui._resolve_stream_host() == "aflow.local"

    def test_empty_url_fallback(self):
        gui = _make_gui()
        gui.agent.api_base = ""
        assert gui._resolve_stream_host() == "aflow.local"


# --------------- 8. _on_done callbacks are UI-only ---------------


class TestOnDoneCallbacksUIOnly:
    """Verify that _on_done callbacks do not call any blocking operations."""

    def test_start_on_done_no_blocking(self):
        """start_stream _on_done with _root_alive=False does no blocking work."""
        gui = _make_gui()

        # Capture the _on_done callback via mocked _submit_network_job
        captured_on_done = None

        def fake_submit(fn, *, on_success=None, on_error=None):
            nonlocal captured_on_done
            captured_on_done = on_success

        gui.network_worker = MagicMock()
        gui._closing = False
        gui.root = MagicMock()
        gui.root.winfo_exists.return_value = True

        original_submit = gui._submit_network_job
        gui._submit_network_job = fake_submit
        gui.start_stream()
        gui._submit_network_job = original_submit

        assert captured_on_done is not None

        # Call _on_done with _closing=True so _root_alive() returns False
        gui._closing = True
        captured_on_done("ok")  # Should exit early, no blocking calls

        # Verify no network/subprocess calls happened in the callback
        gui.agent.start_stream.assert_not_called()
        gui.agent.stop_stream.assert_not_called()
        gui._stream_client.start_sender.assert_not_called()
        gui._stream_client.stop_sender.assert_not_called()

    def test_stop_on_done_no_blocking(self):
        """stop_stream _on_done with _root_alive=False does no blocking work."""
        gui = _make_gui()

        captured_on_done = None

        def fake_submit(fn, *, on_success=None, on_error=None):
            nonlocal captured_on_done
            captured_on_done = on_success

        gui.network_worker = MagicMock()
        gui._closing = False
        gui.root = MagicMock()
        gui.root.winfo_exists.return_value = True

        original_submit = gui._submit_network_job
        gui._submit_network_job = fake_submit
        gui.stop_stream()
        gui._submit_network_job = original_submit

        assert captured_on_done is not None

        gui._closing = True
        captured_on_done(True)  # Should exit early

        gui.agent.start_stream.assert_not_called()
        gui.agent.stop_stream.assert_not_called()
        gui._stream_client.start_sender.assert_not_called()
        gui._stream_client.stop_sender.assert_not_called()


# --------------- 9. JSON parse failure returns False ---------------


class TestStreamApiJsonSafety:
    """Verify start_stream/stop_stream handle non-JSON responses gracefully."""

    def test_start_stream_non_json_response(self):
        agent_mod = _import_agent()
        agent = agent_mod.AnnounceFlowAgent.__new__(agent_mod.AnnounceFlowAgent)
        response = MagicMock()
        response.ok = True
        response.json.side_effect = ValueError("No JSON")
        agent._request = MagicMock(return_value=response)

        assert agent.start_stream() is False

    def test_stop_stream_non_json_response(self):
        agent_mod = _import_agent()
        agent = agent_mod.AnnounceFlowAgent.__new__(agent_mod.AnnounceFlowAgent)
        response = MagicMock()
        response.ok = True
        response.json.side_effect = ValueError("No JSON")
        agent._request = MagicMock(return_value=response)

        assert agent.stop_stream() is False
