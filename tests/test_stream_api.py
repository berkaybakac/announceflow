"""Phase 3 stream API tests — mini gate."""
from unittest.mock import MagicMock, patch

import pytest

from services.stream_service import StreamService, StreamStatus
from stream_manager import StreamManager
from web_panel import app


# --------------- fixtures ---------------


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
    player.stop.return_value = True
    player.stop_playlist.return_value = None
    return player


def _make_service(mock_manager, mock_player):
    return StreamService(
        stream_manager=mock_manager, player_fn=lambda: mock_player
    )


# --------------- StreamService unit tests ---------------


class TestStreamServiceStart:
    def test_start_returns_active_true(self, mock_manager, mock_player):
        svc = _make_service(mock_manager, mock_player)
        result = svc.start()
        assert result["success"] is True
        assert result["status"]["active"] is True
        assert result["status"]["state"] == "live"

    def test_start_passes_explicit_correlation_id_to_manager(
        self, mock_manager, mock_player
    ):
        svc = _make_service(mock_manager, mock_player)
        result = svc.start(correlation_id="cid-explicit")
        assert result["success"] is True
        mock_manager.start_receiver.assert_called_once_with(
            correlation_id="cid-explicit"
        )

    @patch("services.stream_service._new_correlation_id")
    def test_start_generates_correlation_id_when_missing(
        self, mock_new_cid, mock_manager, mock_player
    ):
        mock_new_cid.return_value = "cid-generated"
        svc = _make_service(mock_manager, mock_player)
        result = svc.start()
        assert result["success"] is True
        mock_manager.start_receiver.assert_called_once_with(
            correlation_id="cid-generated"
        )

    def test_start_idempotent(self, mock_manager, mock_player):
        svc = _make_service(mock_manager, mock_player)
        r1 = svc.start()
        r2 = svc.start()
        assert r1["success"] is True
        assert r2["success"] is True
        assert r2["status"]["active"] is True
        mock_manager.start_receiver.assert_called_once()

    def test_start_stops_playlist(self, mock_manager, mock_player):
        mock_player.get_state.return_value = {
            "is_playing": True,
            "playlist": {"active": True},
        }
        svc = _make_service(mock_manager, mock_player)
        result = svc.start()
        mock_player.stop_playlist.assert_called_once()
        assert result["status"]["source_before_stream"] == "playlist"

    def test_start_stops_single_track(self, mock_manager, mock_player):
        mock_player.get_state.return_value = {
            "is_playing": True,
            "playlist": {"active": False},
        }
        svc = _make_service(mock_manager, mock_player)
        svc.start()
        mock_player.stop.assert_called_once()

    def test_start_records_source_none_when_idle(self, mock_manager, mock_player):
        svc = _make_service(mock_manager, mock_player)
        result = svc.start()
        assert result["status"]["source_before_stream"] == "none"

    def test_receiver_failure_sets_error(self, mock_manager, mock_player):
        mock_manager.start_receiver.return_value = False
        svc = _make_service(mock_manager, mock_player)
        result = svc.start()
        assert result["success"] is False
        assert result["status"]["state"] == "error"
        assert result["status"]["last_error"] == "receiver_start_failed"

    @patch("services.stream_service.StreamService._restore_playlist")
    def test_receiver_failure_restores_playlist(
        self, mock_restore, mock_manager, mock_player
    ):
        """P1 fix: if receiver fails, playlist playback is rolled back."""
        mock_manager.start_receiver.return_value = False
        mock_player.get_state.return_value = {
            "is_playing": True,
            "playlist": {"active": True},
        }
        svc = _make_service(mock_manager, mock_player)
        result = svc.start()
        assert result["success"] is False
        mock_player.stop_playlist.assert_called_once()
        mock_restore.assert_called_once()

    def test_receiver_failure_restores_single_track(self, mock_manager, mock_player):
        """P2 fix: if receiver fails during single-track, playback is restored."""
        mock_manager.start_receiver.return_value = False
        mock_player.get_state.return_value = {
            "is_playing": True,
            "current_file": "/media/song.mp3",
            "position": 12.5,
            "playlist": {"active": False},
        }
        svc = _make_service(mock_manager, mock_player)
        result = svc.start()
        assert result["success"] is False
        mock_player.stop.assert_called_once()
        mock_player.play.assert_called_once_with(
            "/media/song.mp3", start_position=12.5
        )


class TestStreamServiceStop:
    def test_stop_returns_active_false(self, mock_manager, mock_player):
        svc = _make_service(mock_manager, mock_player)
        svc.start()
        result = svc.stop()
        assert result["success"] is True
        assert result["status"]["active"] is False
        assert result["status"]["state"] == "idle"

    def test_stop_idempotent(self, mock_manager, mock_player):
        svc = _make_service(mock_manager, mock_player)
        r1 = svc.stop()
        r2 = svc.stop()
        assert r1["success"] is True
        assert r2["success"] is True

    def test_stop_calls_stop_receiver(self, mock_manager, mock_player):
        svc = _make_service(mock_manager, mock_player)
        svc.start()
        svc.stop()
        mock_manager.stop_receiver.assert_called_once()

    def test_stop_receiver_failure_sets_last_error(self, mock_manager, mock_player):
        """P1 fix: stop_receiver failure is reported via last_error and state=error."""
        mock_manager.stop_receiver.return_value = False
        svc = _make_service(mock_manager, mock_player)
        svc.start()
        result = svc.stop()
        assert result["success"] is True  # best-effort: session closed
        assert result["status"]["active"] is False
        assert result["status"]["state"] == "error"
        assert result["status"]["last_error"] == "receiver_stop_failed"


class TestStreamServiceStatus:
    def test_status_reflects_start(self, mock_manager, mock_player):
        mock_manager.is_alive.return_value = True
        svc = _make_service(mock_manager, mock_player)
        svc.start()
        st = svc.status()
        assert st["active"] is True
        assert st["state"] == "live"

    def test_status_reflects_stop(self, mock_manager, mock_player):
        svc = _make_service(mock_manager, mock_player)
        svc.start()
        svc.stop()
        st = svc.status()
        assert st["active"] is False
        assert st["state"] == "idle"

    def test_status_detects_dead_receiver(self, mock_manager, mock_player):
        mock_manager.is_alive.return_value = True
        svc = _make_service(mock_manager, mock_player)
        svc.start()
        mock_manager.is_alive.return_value = False
        st = svc.status()
        assert st["active"] is False
        assert st["state"] == "error"
        assert st["last_error"] == "receiver_died"

    def test_status_contract_keys(self, mock_manager, mock_player):
        svc = _make_service(mock_manager, mock_player)
        st = svc.status()
        assert set(st.keys()) == {
            "active",
            "state",
            "source_before_stream",
            "last_error",
        }


# --------------- HTTP route tests ---------------


class TestStreamRoutes:
    @patch("routes.stream_routes._stream_service")
    def test_start_endpoint_200(self, mock_svc, client):
        mock_svc.start.return_value = {
            "success": True,
            "status": StreamStatus(active=True, state="live").to_dict(),
        }
        resp = client.post("/api/stream/start")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True
        assert data["status"]["active"] is True
        mock_svc.start.assert_called_once_with()

    @patch("routes.stream_routes._stream_service")
    def test_start_endpoint_forwards_correlation_header(self, mock_svc, client):
        mock_svc.start.return_value = {
            "success": True,
            "status": StreamStatus(active=True, state="live").to_dict(),
        }
        resp = client.post(
            "/api/stream/start",
            headers={"X-Stream-Correlation-Id": "cid-route-1"},
        )
        assert resp.status_code == 200
        mock_svc.start.assert_called_once_with(correlation_id="cid-route-1")

    @patch("routes.stream_routes._stream_service")
    def test_stop_endpoint_200(self, mock_svc, client):
        mock_svc.stop.return_value = {
            "success": True,
            "status": StreamStatus(active=False, state="idle").to_dict(),
        }
        resp = client.post("/api/stream/stop")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True

    @patch("routes.stream_routes._stream_service")
    def test_stop_endpoint_returns_error_state_when_receiver_stop_fails(
        self, mock_svc, client
    ):
        """Route contract: best-effort stop may still return state=error."""
        mock_svc.stop.return_value = {
            "success": True,
            "status": StreamStatus(
                active=False,
                state="error",
                last_error="receiver_stop_failed",
            ).to_dict(),
        }
        resp = client.post("/api/stream/stop")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True
        assert data["status"]["active"] is False
        assert data["status"]["state"] == "error"
        assert data["status"]["last_error"] == "receiver_stop_failed"

    @patch("routes.stream_routes._stream_service")
    def test_status_endpoint_contract(self, mock_svc, client):
        mock_svc.status.return_value = StreamStatus().to_dict()
        resp = client.get("/api/stream/status")
        assert resp.status_code == 200
        data = resp.get_json()
        assert set(data.keys()) == {
            "active",
            "state",
            "source_before_stream",
            "last_error",
        }

    def test_start_stop_require_login(self):
        """P3 fix: unauthorized POST start/stop should redirect to login."""
        app.config["TESTING"] = True
        c = app.test_client()
        for path in ["/api/stream/start", "/api/stream/stop"]:
            resp = c.post(path, follow_redirects=False)
            assert resp.status_code in (301, 302)
            assert "/login" in resp.headers.get("Location", "")

    @patch("routes.stream_routes._stream_service")
    def test_start_failure_returns_500(self, mock_svc, client):
        mock_svc.start.return_value = {
            "success": False,
            "status": StreamStatus(
                state="error", last_error="receiver_start_failed"
            ).to_dict(),
        }
        resp = client.post("/api/stream/start")
        assert resp.status_code == 500


# --------------- Mini gate: full lifecycle ---------------


class TestStreamMiniGate:
    """Phase 3 gate: start+status=active, stop+status=idle, idempotent."""

    @patch("routes.stream_routes._stream_service")
    def test_full_lifecycle(self, mock_svc, client):
        status_state = {"current": StreamStatus().to_dict()}

        def fake_start():
            status_state["current"] = StreamStatus(
                active=True, state="live"
            ).to_dict()
            return {"success": True, "status": status_state["current"]}

        def fake_stop():
            status_state["current"] = StreamStatus(
                active=False, state="idle"
            ).to_dict()
            return {"success": True, "status": status_state["current"]}

        mock_svc.start.side_effect = fake_start
        mock_svc.stop.side_effect = fake_stop
        mock_svc.status.side_effect = lambda: status_state["current"]

        # Gate 1: start + status = active:true
        client.post("/api/stream/start")
        resp = client.get("/api/stream/status")
        assert resp.get_json()["active"] is True

        # Gate 2: second start = same (idempotent)
        resp2 = client.post("/api/stream/start")
        assert resp2.get_json()["success"] is True

        # Gate 3: stop + status = active:false
        client.post("/api/stream/stop")
        resp3 = client.get("/api/stream/status")
        assert resp3.get_json()["active"] is False
