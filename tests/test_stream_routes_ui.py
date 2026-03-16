"""Tests for stream route responses — ensures UI contract is met.

The web UI (index.html streamControl component) depends on specific
JSON fields from /api/stream/start, /api/stream/stop, /api/stream/status.
These tests verify the contract between routes and UI.
"""
from unittest.mock import MagicMock, patch

import pytest

from services.stream_service import StreamService, StreamStatus
from stream_manager import StreamManager
from web_panel import app


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


class TestStreamStartRoute:
    """POST /api/stream/start — UI expects {success, status}."""

    @patch("routes.stream_routes._stream_service")
    def test_start_success_has_required_fields(self, mock_svc, client):
        mock_svc.start.return_value = {
            "success": True,
            "status": StreamStatus(active=True, state="live").to_dict(),
        }
        resp = client.post("/api/stream/start")
        data = resp.get_json()
        assert resp.status_code == 200
        assert data["success"] is True
        assert "status" in data
        assert data["status"]["active"] is True
        assert data["status"]["state"] == "live"

    @patch("routes.stream_routes._stream_service")
    def test_start_failure_returns_error(self, mock_svc, client):
        mock_svc.start.return_value = {
            "success": False,
            "status": StreamStatus(
                state="error", last_error="receiver_start_failed"
            ).to_dict(),
        }
        resp = client.post("/api/stream/start")
        assert resp.status_code == 500
        data = resp.get_json()
        assert "error" in data

    @patch("routes.stream_routes._stream_service")
    def test_start_already_live_returns_409(self, mock_svc, client):
        mock_svc.start.return_value = {
            "success": False,
            "error": "stream_already_live",
            "status": StreamStatus(active=True, state="live").to_dict(),
        }
        resp = client.post("/api/stream/start")
        assert resp.status_code == 409
        data = resp.get_json()
        assert data["error"] == "stream_already_live"


class TestStreamStopRoute:
    """POST /api/stream/stop — UI expects {success, status}."""

    @patch("routes.stream_routes._stream_service")
    def test_stop_success_has_required_fields(self, mock_svc, client):
        mock_svc.stop.return_value = {
            "success": True,
            "status": StreamStatus(active=False, state="idle").to_dict(),
        }
        resp = client.post("/api/stream/stop")
        data = resp.get_json()
        assert resp.status_code == 200
        assert data["success"] is True
        assert "status" in data
        assert data["status"]["active"] is False
        assert data["status"]["state"] == "idle"

    @patch("routes.stream_routes._stream_service")
    def test_stop_failure_returns_500(self, mock_svc, client):
        mock_svc.stop.return_value = {
            "success": False,
            "status": StreamStatus(
                state="error", last_error="receiver_stop_failed"
            ).to_dict(),
        }
        resp = client.post("/api/stream/stop")
        assert resp.status_code == 500
        data = resp.get_json()
        assert "error" in data


class TestStreamStatusRoute:
    """GET /api/stream/status — UI reads active, state, last_error."""

    @patch("routes.stream_routes._stream_service")
    def test_status_idle(self, mock_svc, client):
        mock_svc.status.return_value = StreamStatus().to_dict()
        resp = client.get("/api/stream/status")
        data = resp.get_json()
        assert resp.status_code == 200
        assert data["active"] is False
        assert data["state"] == "idle"
        assert data["last_error"] is None

    @patch("routes.stream_routes._stream_service")
    def test_status_live(self, mock_svc, client):
        mock_svc.status.return_value = StreamStatus(
            active=True, state="live"
        ).to_dict()
        resp = client.get("/api/stream/status")
        data = resp.get_json()
        assert data["active"] is True
        assert data["state"] == "live"

    @patch("routes.stream_routes._stream_service")
    def test_status_error_with_last_error(self, mock_svc, client):
        mock_svc.status.return_value = StreamStatus(
            active=False, state="error", last_error="receiver_died"
        ).to_dict()
        resp = client.get("/api/stream/status")
        data = resp.get_json()
        assert data["active"] is False
        assert data["state"] == "error"
        assert data["last_error"] == "receiver_died"

    @patch("routes.stream_routes._stream_service")
    def test_status_paused_for_announcement(self, mock_svc, client):
        mock_svc.status.return_value = StreamStatus(
            active=True, state="paused_for_announcement"
        ).to_dict()
        resp = client.get("/api/stream/status")
        data = resp.get_json()
        assert data["active"] is True
        assert data["state"] == "paused_for_announcement"


class TestStreamUILifecycle:
    """Full start → status → stop → status cycle as UI would call it."""

    @patch("routes.stream_routes._stream_service")
    def test_ui_lifecycle(self, mock_svc, client):
        state = {"current": StreamStatus().to_dict()}

        def fake_start(**kwargs):
            state["current"] = StreamStatus(active=True, state="live").to_dict()
            return {"success": True, "status": state["current"]}

        def fake_stop():
            state["current"] = StreamStatus(active=False, state="idle").to_dict()
            return {"success": True, "status": state["current"]}

        mock_svc.start.side_effect = fake_start
        mock_svc.stop.side_effect = fake_stop
        mock_svc.status.side_effect = lambda: state["current"]

        # 1. Start stream
        resp = client.post("/api/stream/start")
        assert resp.get_json()["success"] is True

        # 2. Poll status — UI reads active + state
        resp = client.get("/api/stream/status")
        data = resp.get_json()
        assert data["active"] is True
        assert data["state"] == "live"

        # 3. Stop stream
        resp = client.post("/api/stream/stop")
        assert resp.get_json()["success"] is True

        # 4. Poll status — should be idle
        resp = client.get("/api/stream/status")
        data = resp.get_json()
        assert data["active"] is False
        assert data["state"] == "idle"


class TestStreamRoutesAuth:
    """All stream endpoints require login."""

    def test_start_requires_auth(self):
        app.config["TESTING"] = True
        c = app.test_client()
        resp = c.post("/api/stream/start", follow_redirects=False)
        assert resp.status_code in (301, 302)
        assert "/login" in resp.headers.get("Location", "")

    def test_stop_requires_auth(self):
        app.config["TESTING"] = True
        c = app.test_client()
        resp = c.post("/api/stream/stop", follow_redirects=False)
        assert resp.status_code in (301, 302)
        assert "/login" in resp.headers.get("Location", "")

    def test_status_requires_auth(self):
        app.config["TESTING"] = True
        c = app.test_client()
        resp = c.get("/api/stream/status", follow_redirects=False)
        assert resp.status_code in (301, 302)
        assert "/login" in resp.headers.get("Location", "")
