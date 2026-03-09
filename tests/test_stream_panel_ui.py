"""Panel stream control tests.

Covers the flows triggered by the web panel's "Yayını Başlat / Durdur"
buttons.  Intentionally does NOT repeat service-unit or route-contract tests
already in test_stream_api.py / test_stream_routes_ui.py.  Focus areas:

  1. Panel-initiated start (no device headers)
  2. Agent heartbeat accepted after panel start
  3. Panel stop can terminate any session (cross-ownership)
  4. Status fields consumed by renderStreamState()
  5. State transitions: idle→live, live→idle, error→live
  6. Policy/announcement states: stop button must be available
"""
import time
from unittest.mock import MagicMock, patch

import pytest

from services.stream_service import HEARTBEAT_TIMEOUT, StreamService, StreamStatus
from stream_manager import StreamManager
from web_panel import app


# --------------- Fixtures ---------------


@pytest.fixture
def client():
    app.config["TESTING"] = True
    with app.test_client() as c:
        with c.session_transaction() as sess:
            sess["logged_in"] = True
        yield c


@pytest.fixture
def mock_manager():
    mgr = MagicMock(spec=StreamManager)
    mgr.start_receiver.return_value = True
    mgr.stop_receiver.return_value = True
    mgr.is_alive.return_value = False
    mgr.wait_for_stop_complete.return_value = None
    return mgr


@pytest.fixture
def mock_player():
    player = MagicMock()
    player.get_state.return_value = {
        "is_playing": False,
        "playlist": {"active": False},
    }
    return player


def _make_service(mock_manager, mock_player):
    return StreamService(
        stream_manager=mock_manager, player_fn=lambda: mock_player
    )


# --------------- 1. Panel-initiated start (no device headers) ---------------


class TestPanelStart:
    def test_start_without_device_id_succeeds(self, mock_manager, mock_player):
        """Panel click → start() with no device_id → receiver starts."""
        svc = _make_service(mock_manager, mock_player)
        result = svc.start()  # no device_id, as panel sends it
        assert result["success"] is True
        assert result["status"]["active"] is True
        assert result["status"]["state"] == "live"

    def test_start_without_device_id_leaves_owner_none(
        self, mock_manager, mock_player
    ):
        """Panel-initiated session has no owner — UI shows 'Panel'."""
        mock_manager.is_alive.return_value = True
        svc = _make_service(mock_manager, mock_player)
        svc.start()
        st = svc.status()
        assert st["owner_device_id"] is None

    def test_start_without_device_id_starts_heartbeat_monitoring(
        self, mock_manager, mock_player
    ):
        """Fix 2: _last_heartbeat_at initialised even without device_id."""
        svc = _make_service(mock_manager, mock_player)
        svc.start()
        assert svc._last_heartbeat_at > 0.0

    def test_panel_start_when_idle_calls_receiver_once(
        self, mock_manager, mock_player
    ):
        svc = _make_service(mock_manager, mock_player)
        svc.start()
        mock_manager.start_receiver.assert_called_once()

    def test_panel_start_via_route_no_device_header(self, client):
        """Route: POST /api/stream/start with no device headers → no device_id passed."""
        with patch("routes.stream_routes._stream_service") as mock_svc:
            mock_svc.start.return_value = {
                "success": True,
                "status": StreamStatus(active=True, state="live").to_dict(),
            }
            resp = client.post("/api/stream/start")  # no X-Stream-Device-Id
            assert resp.status_code == 200
            # Route must call start() with no arguments when headers are absent
            mock_svc.start.assert_called_once_with()


# --------------- 2. Agent heartbeat after panel start ---------------


class TestAgentHeartbeatAfterPanelStart:
    def test_agent_heartbeat_accepted_when_owner_is_none(
        self, mock_manager, mock_player
    ):
        """Panel starts (owner=None), then agent sends heartbeat → accepted."""
        svc = _make_service(mock_manager, mock_player)
        svc.start()  # panel, no device_id → _active_device_id = None
        result = svc.heartbeat(device_id="agent-win-01")
        assert result["accepted"] is True

    def test_agent_heartbeat_refreshes_timer_after_panel_start(
        self, mock_manager, mock_player
    ):
        """Agent heartbeat keeps the stream alive after panel-initiated start."""
        svc = _make_service(mock_manager, mock_player)
        svc.start()
        before = svc._last_heartbeat_at
        time.sleep(0.01)
        svc.heartbeat(device_id="agent-win-01")
        assert svc._last_heartbeat_at > before

    def test_panel_stream_auto_stops_when_nobody_sends_heartbeat(
        self, mock_manager, mock_player
    ):
        """Panel starts but no agent connects → heartbeat expires → auto-stop."""
        svc = _make_service(mock_manager, mock_player)
        svc.start()
        # Expire the heartbeat timer immediately
        svc._last_heartbeat_at = time.monotonic() - (HEARTBEAT_TIMEOUT + 1)
        stopped = svc._check_heartbeat()
        assert stopped is True
        st = svc.status()
        assert st["active"] is False

    def test_agent_start_after_panel_start_becomes_owner(
        self, mock_manager, mock_player
    ):
        """After panel starts, agent calling start() takes ownership (LWW)."""
        mock_manager.is_alive.return_value = True
        svc = _make_service(mock_manager, mock_player)
        svc.start()  # panel
        r = svc.start(device_id="agent-win-01")  # agent takes over
        assert r["success"] is True
        assert r.get("takeover") is True
        assert svc.status()["owner_device_id"] == "agent-win-01"


# --------------- 3. Panel stop: cross-ownership ---------------


class TestPanelStop:
    def test_panel_can_stop_agent_owned_stream(self, mock_manager, mock_player):
        """Stop has no ownership check — panel can terminate any session."""
        svc = _make_service(mock_manager, mock_player)
        svc.start(device_id="agent-win-01")
        result = svc.stop()  # called by panel, no device context
        assert result["success"] is True
        assert result["status"]["active"] is False
        assert result["status"]["state"] == "idle"

    def test_panel_stop_clears_owner_device_id(self, mock_manager, mock_player):
        mock_manager.is_alive.return_value = True
        svc = _make_service(mock_manager, mock_player)
        svc.start(device_id="agent-win-01")
        svc.stop()
        assert svc.status()["owner_device_id"] is None

    def test_panel_stop_during_stopped_by_policy_prevents_resume(
        self, mock_manager, mock_player
    ):
        """User clicks Stop while stream is policy-stopped → won't auto-resume."""
        svc = _make_service(mock_manager, mock_player)
        svc.start(device_id="agent-win-01")
        svc.force_stop_by_policy()
        assert svc._policy_resume_armed is True

        svc.stop()  # explicit panel stop
        assert svc._user_stopped is True
        assert svc._policy_resume_armed is False

        # Policy lifts — stream should NOT restart
        result = svc.resume_after_policy()
        assert result["status"]["state"] != "live"

    def test_panel_stop_during_paused_for_announcement_sets_idle(
        self, mock_manager, mock_player
    ):
        """Panel stop during announcement pause → session fully terminated."""
        svc = _make_service(mock_manager, mock_player)
        svc.start(device_id="agent-win-01")
        svc.pause_for_announcement()
        assert svc._status.state == "paused_for_announcement"

        svc.stop()
        st = svc.status()
        assert st["active"] is False
        # State is idle (or error if stop_receiver returned False, but mock returns True)
        assert st["state"] == "idle"

    def test_panel_stop_via_route(self, client):
        with patch("routes.stream_routes._stream_service") as mock_svc:
            mock_svc.stop.return_value = {
                "success": True,
                "status": StreamStatus(active=False, state="idle").to_dict(),
            }
            resp = client.post("/api/stream/stop")
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["success"] is True
            assert data["status"]["active"] is False


# --------------- 4. Status fields for renderStreamState() ---------------


class TestStatusFieldsForUI:
    """Every field read by renderStreamState() must be present in the response."""

    @patch("routes.stream_routes._stream_service")
    def test_status_has_all_ui_fields(self, mock_svc, client):
        mock_svc.status.return_value = {
            "active": False,
            "state": "idle",
            "last_error": None,
            "owner_device_id": None,
            "source_before_stream": "none",
        }
        resp = client.get("/api/stream/status")
        assert resp.status_code == 200
        data = resp.get_json()
        for field in ("active", "state", "last_error", "owner_device_id"):
            assert field in data, f"Missing field: {field}"

    @patch("routes.stream_routes._stream_service")
    def test_status_live_has_owner_device_id(self, mock_svc, client):
        st = StreamStatus(active=True, state="live").to_dict()
        st["owner_device_id"] = "agent-win-01"
        mock_svc.status.return_value = st
        resp = client.get("/api/stream/status")
        data = resp.get_json()
        assert data["owner_device_id"] == "agent-win-01"

    @patch("routes.stream_routes._stream_service")
    def test_status_error_exposes_last_error(self, mock_svc, client):
        st = StreamStatus(
            active=False, state="error", last_error="receiver_died"
        ).to_dict()
        st["owner_device_id"] = None
        mock_svc.status.return_value = st
        resp = client.get("/api/stream/status")
        data = resp.get_json()
        assert data["state"] == "error"
        assert data["last_error"] == "receiver_died"

    @patch("routes.stream_routes._stream_service")
    def test_status_paused_for_announcement_is_active_true(
        self, mock_svc, client
    ):
        """UI shows Stop button when paused_for_announcement (active=True)."""
        st = StreamStatus(active=True, state="paused_for_announcement").to_dict()
        st["owner_device_id"] = "agent-win-01"
        mock_svc.status.return_value = st
        resp = client.get("/api/stream/status")
        data = resp.get_json()
        assert data["active"] is True
        assert data["state"] == "paused_for_announcement"

    @patch("routes.stream_routes._stream_service")
    def test_status_stopped_by_policy_has_active_false(self, mock_svc, client):
        """UI shows Stop button when stopped_by_policy (active=False, state set)."""
        st = StreamStatus(active=False, state="stopped_by_policy").to_dict()
        st["owner_device_id"] = "agent-win-01"
        mock_svc.status.return_value = st
        resp = client.get("/api/stream/status")
        data = resp.get_json()
        assert data["active"] is False
        assert data["state"] == "stopped_by_policy"


# --------------- 5. State transitions ---------------


class TestPanelStateTransitions:
    def test_idle_to_live(self, mock_manager, mock_player):
        mock_manager.is_alive.return_value = True
        svc = _make_service(mock_manager, mock_player)
        assert svc.status()["state"] == "idle"
        svc.start()
        assert svc.status()["state"] == "live"

    def test_live_to_idle_via_panel_stop(self, mock_manager, mock_player):
        svc = _make_service(mock_manager, mock_player)
        svc.start()
        svc.stop()
        assert svc.status()["state"] == "idle"

    def test_error_to_live_via_panel_start(self, mock_manager, mock_player):
        """After receiver_died error, panel can restart the stream."""
        mock_manager.is_alive.return_value = True
        svc = _make_service(mock_manager, mock_player)
        # Inject error state (simulates receiver crash detected by status())
        svc._status = StreamStatus(
            active=False, state="error", last_error="receiver_died"
        )
        svc._active_device_id = None
        svc._active_correlation_id = None
        r = svc.start()
        assert r["success"] is True
        assert r["status"]["state"] == "live"

    def test_start_failure_leaves_error_state(self, mock_manager, mock_player):
        """Receiver fails to start → state=error, UI should show retry button."""
        mock_manager.start_receiver.return_value = False
        svc = _make_service(mock_manager, mock_player)
        result = svc.start()
        assert result["success"] is False
        assert result["status"]["state"] == "error"
        assert result["status"]["last_error"] == "receiver_start_failed"


# --------------- 6. Policy/announcement stop-button availability ---------------


class TestPolicyStatesHaveStopAction:
    """In paused_for_announcement and stopped_by_policy states the panel
    must be able to call stop() and fully terminate the session."""

    def test_stop_when_paused_for_announcement_succeeds(
        self, mock_manager, mock_player
    ):
        svc = _make_service(mock_manager, mock_player)
        svc.start(device_id="agent-01")
        svc.pause_for_announcement()
        result = svc.stop()
        assert result["success"] is True
        assert result["status"]["active"] is False

    def test_stop_when_stopped_by_policy_succeeds(
        self, mock_manager, mock_player
    ):
        svc = _make_service(mock_manager, mock_player)
        svc.start(device_id="agent-01")
        svc.force_stop_by_policy()
        result = svc.stop()
        assert result["success"] is True
        assert result["status"]["active"] is False

    def test_stop_when_stopped_by_policy_state_becomes_idle(
        self, mock_manager, mock_player
    ):
        svc = _make_service(mock_manager, mock_player)
        svc.start(device_id="agent-01")
        svc.force_stop_by_policy()
        svc.stop()
        assert svc.status()["state"] == "idle"
