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
    agent_mock.api_base = "http://stateksound.local:5001"
    agent_mock.config = {"api_base": "http://stateksound.local:5001"}

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
    gui._status_clear_job = None
    gui._btn_music_start = None
    gui._btn_music_stop = None
    gui._btn_stream_start = None
    gui._btn_stream_stop = None
    gui._btn_upload = None

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
        gui._stream_client.start_sender.assert_called_once_with("stateksound.local", 5800)
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
        gui.agent.api_base = "http://stateksound.local:5001"
        assert gui._resolve_stream_host() == "stateksound.local"

    def test_ip_url(self):
        gui = _make_gui()
        gui.agent.api_base = "http://192.168.1.50:5001"
        assert gui._resolve_stream_host() == "192.168.1.50"


# --------------- 7. _resolve_stream_host: invalid URL fallback ---------------


    def test_invalid_url_fallback(self):
        gui = _make_gui()
        gui.agent.api_base = "not-a-valid-url"
        assert gui._resolve_stream_host() == "stateksound.local"

    def test_empty_url_fallback(self):
        gui = _make_gui()
        gui.agent.api_base = ""
        assert gui._resolve_stream_host() == "stateksound.local"


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


class TestStreamApiDetailedWrappers:
    """Coverage for start_stream_with_details/stop_stream_with_details."""

    def test_start_stream_with_details_success_payload(self):
        agent_mod = _import_agent()
        agent = agent_mod.AnnounceFlowAgent.__new__(agent_mod.AnnounceFlowAgent)
        agent.device_id = "dev-test-1"
        response = MagicMock()
        response.ok = True
        response.status_code = 200
        response.json.return_value = {"success": True, "status": {"state": "live"}}
        agent._request = MagicMock(return_value=response)

        result = agent.start_stream_with_details()
        assert result["success"] is True
        assert result["http_status"] == 200
        assert result["status"]["state"] == "live"
        agent._request.assert_called_once_with(
            "POST",
            "/api/stream/start",
            auth_required=True,
            headers={"X-Stream-Device-Id": "dev-test-1"},
        )

    def test_start_stream_with_details_connection_failure(self):
        agent_mod = _import_agent()
        agent = agent_mod.AnnounceFlowAgent.__new__(agent_mod.AnnounceFlowAgent)
        agent.device_id = "dev-test-2"
        agent._request = MagicMock(return_value=None)

        result = agent.start_stream_with_details()
        assert result == {"success": False, "error": "api_start_failed"}

    def test_start_stream_with_details_sends_correlation_and_device_headers(self):
        agent_mod = _import_agent()
        agent = agent_mod.AnnounceFlowAgent.__new__(agent_mod.AnnounceFlowAgent)
        agent.device_id = "dev-test-3"
        response = MagicMock()
        response.ok = True
        response.status_code = 200
        response.json.return_value = {"success": True, "status": {"state": "live"}}
        agent._request = MagicMock(return_value=response)

        result = agent.start_stream_with_details(correlation_id="cid-test-1")
        assert result["success"] is True
        agent._request.assert_called_once_with(
            "POST",
            "/api/stream/start",
            auth_required=True,
            headers={
                "X-Stream-Correlation-Id": "cid-test-1",
                "X-Stream-Device-Id": "dev-test-3",
            },
        )

    def test_stop_stream_with_details_invalid_json(self):
        agent_mod = _import_agent()
        agent = agent_mod.AnnounceFlowAgent.__new__(agent_mod.AnnounceFlowAgent)
        response = MagicMock()
        response.ok = True
        response.status_code = 200
        response.json.side_effect = ValueError("No JSON")
        agent._request = MagicMock(return_value=response)

        result = agent.stop_stream_with_details()
        assert result["success"] is False
        assert result["error"] == "api_stop_invalid_response"
        assert result["http_status"] == 200

    def test_send_heartbeat_with_details_not_owner(self):
        agent_mod = _import_agent()
        agent = agent_mod.AnnounceFlowAgent.__new__(agent_mod.AnnounceFlowAgent)
        agent.device_id = "dev-heartbeat-1"
        response = MagicMock()
        response.ok = False
        response.status_code = 409
        response.json.return_value = {"success": False, "error": "not_stream_owner"}
        agent._request = MagicMock(return_value=response)

        result = agent.send_heartbeat_with_details()
        assert result["success"] is False
        assert result["error"] == "not_stream_owner"
        assert result["http_status"] == 409

    def test_send_heartbeat_with_details_success(self):
        agent_mod = _import_agent()
        agent = agent_mod.AnnounceFlowAgent.__new__(agent_mod.AnnounceFlowAgent)
        agent.device_id = "dev-heartbeat-2"
        response = MagicMock()
        response.ok = True
        response.status_code = 200
        response.json.return_value = {"success": True, "status": {"state": "live"}}
        agent._request = MagicMock(return_value=response)

        result = agent.send_heartbeat_with_details()
        assert result["success"] is True
        assert result["status"]["state"] == "live"
        assert result["http_status"] == 200


class TestGuiUsesDetailedStreamApi:
    """Ensure GUI stream jobs use detailed API wrappers."""

    def test_start_stream_job_uses_start_stream_with_details(self):
        gui = _make_gui()
        gui.agent.start_stream_with_details.return_value = {
            "success": False,
            "error": "api_start_failed",
        }
        gui.agent.start_stream.return_value = True  # Should never be called

        captured_job = None

        def fake_submit(fn, *, on_success=None, on_error=None):
            nonlocal captured_job
            captured_job = fn

        gui.network_worker = MagicMock()
        gui._closing = False
        gui.root = MagicMock()
        gui.root.winfo_exists.return_value = True

        original_submit = gui._submit_network_job
        gui._submit_network_job = fake_submit
        gui.start_stream()
        gui._submit_network_job = original_submit

        assert captured_job is not None
        result = captured_job()
        assert result["result"] == "api_fail"
        gui.agent.start_stream_with_details.assert_called_once()
        gui.agent.start_stream.assert_not_called()

    def test_start_stream_api_takeover_in_progress_message(self):
        """GUI should show retry-friendly message for takeover_in_progress."""
        gui = _make_gui()
        gui._show_status = MagicMock()

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
        captured_on_done(
            {
                "result": "api_fail",
                "api": {"error": "takeover_in_progress", "http_status": 409},
            }
        )

        assert gui._show_status.called
        shown = gui._show_status.call_args[0][0]
        assert "tekrar deneyin" in shown.lower()

    @patch("agent.time.sleep", return_value=None)
    def test_stop_stream_job_uses_stop_stream_with_details(self, _mock_sleep):
        gui = _make_gui()
        gui.agent.stop_stream_with_details.return_value = {"success": True}
        gui.agent.stop_stream.return_value = False  # Should never be called

        captured_job = None

        def fake_submit(fn, *, on_success=None, on_error=None):
            nonlocal captured_job
            captured_job = fn

        gui.network_worker = MagicMock()
        gui._closing = False
        gui.root = MagicMock()
        gui.root.winfo_exists.return_value = True

        original_submit = gui._submit_network_job
        gui._submit_network_job = fake_submit
        gui.stop_stream()
        gui._submit_network_job = original_submit

        assert captured_job is not None
        payload = captured_job()
        assert payload["success"] is True
        gui._stream_client.stop_sender.assert_called_once()
        gui.agent.stop_stream_with_details.assert_called_once()
        gui.agent.stop_stream.assert_not_called()


# --------------- helpers for status poll tests ---------------


def _make_gui_for_polling():
    """Create a GUI object ready for _run_status_poll / _on_done testing."""
    agent_mod = _import_agent()
    AgentGUI = agent_mod.AgentGUI

    agent_mock = MagicMock()
    agent_mock.api_base = "http://stateksound.local:5001"
    agent_mock.device_id = "agent-device-abc"
    agent_mock.config = {"api_base": "http://stateksound.local:5001"}

    gui = AgentGUI.__new__(AgentGUI)
    gui.agent = agent_mock
    gui._closing = False
    gui._volume_update_job = None
    gui._pending_volume = None
    gui._status_clear_job = None
    gui._stream_active = True
    gui._stream_poll_active = True
    gui._last_known_owner = None
    gui._heartbeat_job = None
    gui._poll_job = None
    gui._btn_stream_start = None
    gui._btn_stream_stop = None
    gui._btn_music_start = None
    gui._btn_music_stop = None
    gui._btn_upload = None

    # Root mock — winfo_exists returns True so _root_alive() passes
    root_mock = MagicMock()
    root_mock.winfo_exists.return_value = True
    gui.root = root_mock

    # Status label mock for _show_status
    gui._status_label = MagicMock()
    gui._status_label.winfo_exists.return_value = True

    from stream_client import StreamClient
    gui._stream_client = MagicMock(spec=StreamClient)
    gui.network_worker = MagicMock()

    return gui


def _capture_poll_on_done(gui):
    """Call _run_status_poll and capture the _on_done callback.

    The first submit call queues the status-fetch job and captures _on_done.
    All subsequent submit calls are captured (fn + on_success) so tests can
    inspect and execute them manually.
    """
    captured_on_done = None
    submit_calls = []

    def fake_submit(fn, *, on_success=None, on_error=None):
        submit_calls.append((fn, on_success))
        nonlocal captured_on_done
        if len(submit_calls) == 1:
            # First call: status-fetch job → capture on_done, don't execute
            captured_on_done = on_success

    gui._submit_network_job = fake_submit
    gui._run_status_poll()

    return captured_on_done, submit_calls


def _capture_poll_callbacks(gui):
    """Capture both status poll callbacks without executing jobs."""
    captured_on_done = None
    captured_on_error = None

    def fake_submit(fn, *, on_success=None, on_error=None):
        nonlocal captured_on_done, captured_on_error
        captured_on_done = on_success
        captured_on_error = on_error

    gui._submit_network_job = fake_submit
    gui._run_status_poll()
    return captured_on_done, captured_on_error


def _capture_heartbeat_callbacks(gui):
    """Capture heartbeat callbacks and any follow-up submits."""
    captured_on_done = None
    submit_calls = []

    def fake_submit(fn, *, on_success=None, on_error=None):
        nonlocal captured_on_done
        submit_calls.append((fn, on_success, on_error))
        if len(submit_calls) == 1:
            captured_on_done = on_success

    gui._submit_network_job = fake_submit
    gui._run_heartbeat()
    return captured_on_done, submit_calls


# --------------- 9b. Heartbeat remote-stop handling -----------------------


class TestHeartbeatRemoteStop:
    def test_no_active_stream_deactivates_local_stream(self):
        gui = _make_gui_for_polling()
        gui._stream_active = True
        fake_job_id = object()
        gui._heartbeat_job = fake_job_id

        on_done, submits = _capture_heartbeat_callbacks(gui)
        assert on_done is not None

        on_done(
            {
                "success": False,
                "error": "no_active_stream",
                "status": {"active": False, "state": "idle"},
            }
        )

        # Heartbeat is stopped and local stream is marked inactive.
        gui.root.after_cancel.assert_called_with(fake_job_id)
        assert gui._heartbeat_job is None
        assert gui._stream_active is False

        # stop_sender job must be submitted after heartbeat response.
        assert len(submits) >= 2
        stop_fn, _, _ = submits[1]
        stop_fn()
        gui._stream_client.stop_sender.assert_called_once()

    def test_inactive_status_payload_also_deactivates(self):
        gui = _make_gui_for_polling()
        gui._stream_active = True

        on_done, submits = _capture_heartbeat_callbacks(gui)
        assert on_done is not None

        on_done(
            {
                "success": False,
                "error": "heartbeat_failed",
                "status": {"active": False, "state": "error"},
            }
        )

        assert gui._stream_active is False
        assert len(submits) >= 2
        stop_fn, _, _ = submits[1]
        stop_fn()
        gui._stream_client.stop_sender.assert_called_once()


# --------------- 9c. Status poll error resiliency -------------------------


class TestStatusPollResiliency:
    def test_status_poll_on_error_reschedules_polling(self):
        gui = _make_gui_for_polling()
        _, on_error = _capture_poll_callbacks(gui)
        assert on_error is not None

        on_error(RuntimeError("network"))

        gui.root.after.assert_called_once_with(3000, gui._run_status_poll)

    def test_status_poll_non_dict_payload_does_not_crash(self):
        gui = _make_gui_for_polling()
        on_done, _ = _capture_poll_on_done(gui)
        assert on_done is not None

        on_done("invalid-payload")

        assert gui._stream_active is False
        gui.root.after.assert_called_with(3000, gui._run_status_poll)


# --------------- 10. External stop detection (panel/panel stop) ---------------


class TestStatusPollExternalStop:
    """_run_status_poll detects when server stops stream externally."""

    def test_idle_state_stops_local_sender(self):
        """Panel called /api/stream/stop → server idle → EXE stops sender."""
        gui = _make_gui_for_polling()
        on_done, submits = _capture_poll_on_done(gui)
        assert on_done is not None, "_run_status_poll did not register _on_done"

        on_done({"active": False, "state": "idle", "owner_device_id": None})

        # Execute the submitted stop_sender job
        assert len(submits) >= 2, "Should have submitted a stop_sender job"
        stop_job, _ = submits[1]
        stop_job()

        assert gui._stream_active is False
        gui._stream_client.stop_sender.assert_called_once()
        # Poll keeps running for auto-resume
        assert gui._stream_poll_active is True

    def test_error_state_stops_local_sender(self):
        """Receiver died on server → error state → EXE stops sender."""
        gui = _make_gui_for_polling()
        on_done, submits = _capture_poll_on_done(gui)
        assert on_done is not None

        on_done({"active": False, "state": "error", "owner_device_id": None})

        # Execute the submitted stop_sender job
        assert len(submits) >= 2, "Should have submitted a stop_sender job"
        stop_job, _ = submits[1]
        stop_job()

        assert gui._stream_active is False
        gui._stream_client.stop_sender.assert_called_once()

    def test_stopped_by_policy_does_not_stop_sender(self):
        """Policy stop → sender keeps running (receiver will auto-resume)."""
        gui = _make_gui_for_polling()
        on_done, _ = _capture_poll_on_done(gui)
        assert on_done is not None

        on_done({"active": False, "state": "stopped_by_policy", "owner_device_id": None})

        # _stream_active must stay True so heartbeats and polling continue
        assert gui._stream_active is True
        gui._stream_client.stop_sender.assert_not_called()

    def test_live_active_no_stop(self):
        """Stream still live and owned by same device → no action."""
        gui = _make_gui_for_polling()
        gui.agent.device_id = "agent-device-abc"
        on_done, _ = _capture_poll_on_done(gui)
        assert on_done is not None

        on_done({
            "active": True,
            "state": "live",
            "owner_device_id": "agent-device-abc",
        })

        assert gui._stream_active is True
        gui._stream_client.stop_sender.assert_not_called()

    def test_idle_stops_heartbeat_but_keeps_poll(self):
        """On external idle stop, heartbeat is cancelled but poll stays active."""
        gui = _make_gui_for_polling()
        fake_job_id = object()
        gui._heartbeat_job = fake_job_id
        on_done, _ = _capture_poll_on_done(gui)
        assert on_done is not None

        on_done({"active": False, "state": "idle", "owner_device_id": None})

        gui.root.after_cancel.assert_called_with(fake_job_id)
        assert gui._heartbeat_job is None
        # Poll should still be active for auto-resume
        assert gui._stream_poll_active is True

    def test_takeover_stops_poll_completely(self):
        """When another device takes over, local sender stops but polling keeps checking for updates."""
        gui = _make_gui_for_polling()
        gui.agent.device_id = "agent-device-abc"
        on_done, submits = _capture_poll_on_done(gui)
        assert on_done is not None

        on_done({
            "active": True,
            "state": "live",
            "owner_device_id": "other-device-xyz",
        })

        # Execute the submitted stop_sender job
        assert len(submits) >= 2, "Should have submitted a stop_sender job"
        stop_job, _ = submits[1]
        stop_job()

        assert gui._stream_active is False
        assert gui._stream_poll_active is True
        gui._stream_client.stop_sender.assert_called_once()


# --------------- 13. Auto-resume: owner is me, agent resumes sender -----


class TestStatusPollAutoResume:
    """Auto-resume should run only when this device owns the live stream."""

    def test_auto_resume_when_receiver_live_sender_stopped(self):
        """Live + owner is me + sender stopped -> resume sender."""
        gui = _make_gui_for_polling()
        gui._stream_active = False  # Sender stopped (e.g., after panel stop)
        gui._stream_poll_active = True  # But poll still running

        gui._stream_client.start_sender.return_value = True

        on_done, submits = _capture_poll_on_done(gui)
        assert on_done is not None

        on_done(
            {
                "active": True,
                "state": "live",
                "owner_device_id": "agent-device-abc",
            }
        )

        # Auto-resume should have submitted a job that calls start_sender
        assert len(submits) >= 2, "Auto-resume should submit a resume job"
        resume_fn = submits[1][0]
        resume_fn()  # Execute the resume job
        gui._stream_client.start_sender.assert_called_once()

    def test_auto_resume_sets_stream_active_on_success(self):
        """After successful resume, _stream_active should be True."""
        gui = _make_gui_for_polling()
        gui._stream_active = False
        gui._stream_poll_active = True
        gui._stream_client.start_sender.return_value = True

        on_done, submits = _capture_poll_on_done(gui)
        on_done(
            {
                "active": True,
                "state": "live",
                "owner_device_id": "agent-device-abc",
            }
        )

        # Execute resume job
        resume_fn, resume_on_done = submits[1]
        result = resume_fn()
        assert result is True
        # Call the resume callback
        resume_on_done(True)
        assert gui._stream_active is True

    def test_auto_resume_failure_keeps_polling(self):
        """If sender fails to resume, keep polling to retry."""
        gui = _make_gui_for_polling()
        gui._stream_active = False
        gui._stream_poll_active = True
        gui._stream_client.start_sender.return_value = False

        on_done, submits = _capture_poll_on_done(gui)
        on_done(
            {
                "active": True,
                "state": "live",
                "owner_device_id": "agent-device-abc",
            }
        )

        resume_fn, resume_on_done = submits[1]
        result = resume_fn()
        assert result is False
        resume_on_done(False)
        # _stream_active stays False (will retry on next poll)
        assert gui._stream_active is False
        assert gui._stream_poll_active is True

    def test_no_auto_resume_when_idle(self):
        """When receiver is idle and sender was stopped, no resume happens."""
        gui = _make_gui_for_polling()
        gui._stream_active = False
        gui._stream_poll_active = True

        on_done, submits = _capture_poll_on_done(gui)
        on_done({"active": False, "state": "idle", "owner_device_id": None})

        # Only the status-fetch submit, no resume submit
        assert len(submits) == 1
        gui._stream_client.start_sender.assert_not_called()

    def test_no_auto_resume_when_owner_unknown(self):
        """Live + no owner info should not auto-resume to avoid multi-agent overlap."""
        gui = _make_gui_for_polling()
        gui._stream_active = False
        gui._stream_poll_active = True

        on_done, submits = _capture_poll_on_done(gui)
        on_done({"active": True, "state": "live", "owner_device_id": None})

        assert len(submits) == 1
        gui._stream_client.start_sender.assert_not_called()

    def test_full_flow_stop_then_owner_live(self):
        """E2E: exe streaming -> idle stop -> live as same owner -> auto-resume."""
        gui = _make_gui_for_polling()
        gui._stream_active = True
        gui._stream_poll_active = True

        # Step 1: Panel stops stream — agent detects idle
        on_done1, submits1 = _capture_poll_on_done(gui)
        on_done1({"active": False, "state": "idle", "owner_device_id": None})
        assert gui._stream_active is False
        assert gui._stream_poll_active is True  # Poll stays active!

        # Step 2: Stream is live again and owned by this device
        gui._stream_client.start_sender.return_value = True
        gui._stream_client.reset_mock()
        on_done2, submits2 = _capture_poll_on_done(gui)
        on_done2(
            {
                "active": True,
                "state": "live",
                "owner_device_id": "agent-device-abc",
            }
        )

        # Auto-resume triggered
        assert len(submits2) >= 2
        resume_fn, resume_cb = submits2[1]
        resume_fn()
        gui._stream_client.start_sender.assert_called_once()
        resume_cb(True)
        assert gui._stream_active is True


# --------------- 11. Hostname display ---------------


class TestHostnameDisplay:
    """_resolve_stream_host extracts hostname for header label."""

    def test_hostname_from_local_url(self):
        gui = _make_gui_for_polling()
        gui.agent.api_base = "http://stateksound.local:5001"
        assert gui._resolve_stream_host() == "stateksound.local"

    def test_hostname_from_ip_url(self):
        gui = _make_gui_for_polling()
        gui.agent.api_base = "http://192.168.1.42:5001"
        assert gui._resolve_stream_host() == "192.168.1.42"

    def test_hostname_fallback_on_empty(self):
        gui = _make_gui_for_polling()
        gui.agent.api_base = ""
        assert gui._resolve_stream_host() == "stateksound.local"


# --------------- 12. ModernSlider card_bg ---------------


class TestModernSliderCardBg:
    """ModernSlider source has card_bg parameter and _BG_CARD is passed at call site."""

    def _source(self):
        path = os.path.join(_agent_dir, "agent.py")
        with open(path) as f:
            return f.read()

    def test_modernslider_accepts_card_bg_param(self):
        """ModernSlider.__init__ signature includes card_bg parameter."""
        assert "card_bg=None" in self._source()

    def test_vol_section_passes_card_bg(self):
        """show_main_frame passes card_bg=_BG_CARD when creating the volume slider."""
        assert "card_bg=_BG_CARD" in self._source()

    def test_slider_bg_uses_card_bg_or_fallback(self):
        """ModernSlider sets bg from card_bg argument (not hardcoded _BG)."""
        source = self._source()
        # The init should store bg = card_bg or _BG
        assert "bg = card_bg or _BG" in source


# --------------- 13. Volume sync poll ---------------


class TestVolumeSyncPoll:
    """Background volume polling keeps EXE slider consistent with web changes."""

    def _capture_volume_poll_callbacks(self, gui):
        captured_success = None
        captured_error = None

        def fake_submit(fn, *, on_success=None, on_error=None):
            nonlocal captured_success, captured_error
            captured_success = on_success
            captured_error = on_error

        gui._submit_network_job = fake_submit
        gui._run_volume_poll()
        return captured_success, captured_error

    def test_remote_volume_updates_slider(self):
        gui = _make_gui_for_polling()
        gui._volume_poll_active = True
        gui._volume_poll_job = None
        gui._volume_poll_failures = 0
        gui._volume_local_change_until = 0.0
        gui._music_active = False
        gui.volume_slider = MagicMock()
        gui.volume_slider.value = 30
        gui._schedule_next_volume_poll = MagicMock()
        gui._refresh_music_buttons = MagicMock()

        on_success, _ = self._capture_volume_poll_callbacks(gui)
        assert on_success is not None

        on_success({"volume": 55, "playlist": {"active": True}})

        gui.volume_slider.set_value.assert_called_once_with(55)
        assert gui._music_active is True
        gui._refresh_music_buttons.assert_called_once()
        gui._schedule_next_volume_poll.assert_called_once_with(
            gui._VOLUME_POLL_INTERVAL_MS
        )

    def test_local_cooldown_prevents_remote_overwrite(self):
        gui = _make_gui_for_polling()
        gui._volume_poll_active = True
        gui._volume_poll_job = None
        gui._volume_poll_failures = 0
        gui._volume_local_change_until = float("inf")
        gui._music_active = False
        gui.volume_slider = MagicMock()
        gui.volume_slider.value = 30
        gui._schedule_next_volume_poll = MagicMock()
        gui._refresh_music_buttons = MagicMock()

        on_success, _ = self._capture_volume_poll_callbacks(gui)
        assert on_success is not None

        on_success({"volume": 55, "playlist": {"active": True}})

        gui.volume_slider.set_value.assert_not_called()
        assert gui._music_active is True
        gui._refresh_music_buttons.assert_called_once()
        gui._schedule_next_volume_poll.assert_called_once_with(
            gui._VOLUME_POLL_INTERVAL_MS
        )

    def test_poll_error_applies_backoff(self):
        gui = _make_gui_for_polling()
        gui._volume_poll_active = True
        gui._volume_poll_job = None
        gui._volume_poll_failures = 0
        gui._schedule_next_volume_poll = MagicMock()
        gui._music_active = False
        gui._refresh_music_buttons = MagicMock()

        _, on_error = self._capture_volume_poll_callbacks(gui)
        assert on_error is not None

        on_error(RuntimeError("network"))

        assert gui._volume_poll_failures == 1
        gui._refresh_music_buttons.assert_not_called()
        gui._schedule_next_volume_poll.assert_called_once_with(
            gui._VOLUME_POLL_INTERVAL_MS * 2
        )


# --------------- 14. _last_known_owner poll deduplication ---------------


class TestPollLastKnownOwner:
    """Tests for _last_known_owner flag preventing repeated takeover messages."""

    def test_first_remote_stream_detected_shows_message(self):
        """Device B (not streaming) sees Device A streaming → shows 'Başka cihaz yayında' once."""
        gui = _make_gui_for_polling()
        gui._stream_active = False  # B is not streaming
        gui._last_known_owner = None
        gui.agent.device_id = "device-B"
        gui._show_status = MagicMock()

        on_done, submits = _capture_poll_on_done(gui)
        on_done({
            "active": True,
            "state": "live",
            "owner_device_id": "device-A",
        })

        gui._show_status.assert_called_once_with("Başka cihaz yayında")
        assert gui._last_known_owner == "device-A"
        assert gui._stream_active is False

    def test_repeated_poll_same_owner_no_message(self):
        """Second poll with same owner → no repeated message."""
        gui = _make_gui_for_polling()
        gui._stream_active = False
        gui._last_known_owner = "device-A"  # Already know about A
        gui.agent.device_id = "device-B"
        gui._show_status = MagicMock()

        on_done, submits = _capture_poll_on_done(gui)
        on_done({
            "active": True,
            "state": "live",
            "owner_device_id": "device-A",
        })

        gui._show_status.assert_not_called()
        assert gui._last_known_owner == "device-A"
        # Poll continues
        gui.root.after.assert_called()

    def test_owner_change_triggers_new_message(self):
        """Known owner A → owner changes to C → new message shown."""
        gui = _make_gui_for_polling()
        gui._stream_active = False
        gui._last_known_owner = "device-A"
        gui.agent.device_id = "device-B"
        gui._show_status = MagicMock()

        on_done, submits = _capture_poll_on_done(gui)
        on_done({
            "active": True,
            "state": "live",
            "owner_device_id": "device-C",
        })

        gui._show_status.assert_called_once_with("Başka cihaz yayında")
        assert gui._last_known_owner == "device-C"

    def test_remote_stop_shows_yayin_durduruldu(self):
        """Known owner A stops streaming → 'Yayın durduruldu' shown, _last_known_owner cleared."""
        gui = _make_gui_for_polling()
        gui._stream_active = False
        gui._last_known_owner = "device-A"
        gui.agent.device_id = "device-B"
        gui._show_status = MagicMock()

        on_done, submits = _capture_poll_on_done(gui)
        on_done({
            "active": False,
            "state": "idle",
            "owner_device_id": None,
        })

        gui._show_status.assert_called_once_with("Yayın durduruldu")
        assert gui._last_known_owner is None

    def test_takeover_while_actively_streaming_stops_sender(self):
        """Device was streaming, another device takes over → stops sender, shows error."""
        gui = _make_gui_for_polling()
        gui._stream_active = True  # This device WAS streaming
        gui._last_known_owner = None
        gui.agent.device_id = "device-B"
        gui._show_status = MagicMock()

        on_done, submits = _capture_poll_on_done(gui)
        on_done({
            "active": True,
            "state": "live",
            "owner_device_id": "device-A",
        })

        assert gui._stream_active is False
        assert gui._last_known_owner == "device-A"
        # Should have submitted stop_sender job
        assert len(submits) >= 2
        stop_fn, stop_cb = submits[1]
        stop_fn()
        gui._stream_client.stop_sender.assert_called_once()

    def test_stop_stream_clears_last_known_owner(self):
        """User clicks stop → _last_known_owner is cleared."""
        gui = _make_gui_for_polling()
        gui._stream_active = True
        gui._last_known_owner = "device-A"
        gui._show_status = MagicMock()

        # Capture stop_stream's _on_done callback
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
        captured_on_done({"success": True})

        assert gui._last_known_owner is None

    def test_after_remote_stop_new_stream_detected_again(self):
        """After remote stop (A stops) → idle → A starts again → detected as new."""
        gui = _make_gui_for_polling()
        gui._stream_active = False
        gui._last_known_owner = "device-A"
        gui.agent.device_id = "device-B"
        gui._show_status = MagicMock()

        # Step 1: Remote stop
        on_done1, _ = _capture_poll_on_done(gui)
        on_done1({
            "active": False,
            "state": "idle",
            "owner_device_id": None,
        })

        gui._show_status.assert_called_once_with("Yayın durduruldu")
        assert gui._last_known_owner is None
        gui._show_status.reset_mock()

        # Step 2: Same device starts again
        on_done2, _ = _capture_poll_on_done(gui)
        on_done2({
            "active": True,
            "state": "live",
            "owner_device_id": "device-A",
        })

        gui._show_status.assert_called_once_with("Başka cihaz yayında")
        assert gui._last_known_owner == "device-A"

    def test_no_false_remote_stop_when_never_knew_owner(self):
        """If _last_known_owner is None and stream is idle, no 'Yayın durduruldu' shown."""
        gui = _make_gui_for_polling()
        gui._stream_active = False
        gui._last_known_owner = None
        gui.agent.device_id = "device-B"
        gui._show_status = MagicMock()

        on_done, submits = _capture_poll_on_done(gui)
        on_done({
            "active": False,
            "state": "idle",
            "owner_device_id": None,
        })

        gui._show_status.assert_not_called()

    def test_full_flow_remote_lifecycle(self):
        """E2E: idle → A starts → A stops → C starts → C stops.

        Verifies the complete lifecycle with owner tracking.
        """
        gui = _make_gui_for_polling()
        gui._stream_active = False
        gui._last_known_owner = None
        gui.agent.device_id = "device-B"
        gui._show_status = MagicMock()

        # 1. Idle — no message
        on_done, _ = _capture_poll_on_done(gui)
        on_done({"active": False, "state": "idle", "owner_device_id": None})
        gui._show_status.assert_not_called()

        # 2. A starts streaming — "Başka cihaz yayında"
        gui._show_status.reset_mock()
        on_done, _ = _capture_poll_on_done(gui)
        on_done({"active": True, "state": "live", "owner_device_id": "device-A"})
        gui._show_status.assert_called_once_with("Başka cihaz yayında")
        assert gui._last_known_owner == "device-A"

        # 3. A still streaming — no repeated message
        gui._show_status.reset_mock()
        on_done, _ = _capture_poll_on_done(gui)
        on_done({"active": True, "state": "live", "owner_device_id": "device-A"})
        gui._show_status.assert_not_called()

        # 4. A stops — "Yayın durduruldu"
        gui._show_status.reset_mock()
        on_done, _ = _capture_poll_on_done(gui)
        on_done({"active": False, "state": "idle", "owner_device_id": None})
        gui._show_status.assert_called_once_with("Yayın durduruldu")
        assert gui._last_known_owner is None

        # 5. C starts streaming — "Başka cihaz yayında"
        gui._show_status.reset_mock()
        on_done, _ = _capture_poll_on_done(gui)
        on_done({"active": True, "state": "live", "owner_device_id": "device-C"})
        gui._show_status.assert_called_once_with("Başka cihaz yayında")
        assert gui._last_known_owner == "device-C"

        # 6. C stops — "Yayın durduruldu"
        gui._show_status.reset_mock()
        on_done, _ = _capture_poll_on_done(gui)
        on_done({"active": False, "state": "idle", "owner_device_id": None})
        gui._show_status.assert_called_once_with("Yayın durduruldu")
        assert gui._last_known_owner is None

    def test_takeover_then_external_stop_no_double_message(self):
        """P2 regression: B sees A → B takes over → panel stops → only ONE 'Yayın durduruldu'.

        Without the fix (clearing _last_known_owner on start), this would
        produce a second 'Yayın durduruldu' from case 1b on the next poll.
        """
        gui = _make_gui_for_polling()
        gui._stream_active = False
        gui._last_known_owner = None
        gui.agent.device_id = "device-B"
        gui._show_status = MagicMock()

        # Step 1: B sees A streaming
        on_done, _ = _capture_poll_on_done(gui)
        on_done({"active": True, "state": "live", "owner_device_id": "device-A"})
        gui._show_status.assert_called_once_with("Başka cihaz yayında")
        assert gui._last_known_owner == "device-A"

        # Step 2: B starts streaming (takeover) — simulate start success callback
        gui._stream_active = True
        gui._last_known_owner = None  # This is what the fix does at agent.py:1743
        gui._show_status.reset_mock()

        # Step 3: Panel stops stream externally → case 2 fires
        on_done2, submits2 = _capture_poll_on_done(gui)
        on_done2({"active": False, "state": "idle", "owner_device_id": None})
        # Case 2 sets _stream_active = False, submits stop_sender
        assert gui._stream_active is False

        # Step 4: Next poll — case 1b should NOT fire (no stale _last_known_owner)
        gui._show_status.reset_mock()
        on_done3, _ = _capture_poll_on_done(gui)
        on_done3({"active": False, "state": "idle", "owner_device_id": None})

        # No message should be shown — _last_known_owner was cleared at start
        gui._show_status.assert_not_called()
