"""Phase 3 stream API tests — mini gate."""
import time
from unittest.mock import MagicMock, patch

import pytest

from services.stream_service import HEARTBEAT_TIMEOUT, StreamService, StreamStatus
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
    mgr.wait_for_stop_complete.return_value = None
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

    def test_start_twice_without_device_id_causes_takeover(
        self, mock_manager, mock_player
    ):
        """Second start without device_id takes over the existing session."""
        svc = _make_service(mock_manager, mock_player)
        r1 = svc.start()
        r2 = svc.start()
        assert r1["success"] is True
        assert r2["success"] is True
        assert r2.get("takeover") is True
        assert mock_manager.stop_receiver.call_count == 1
        assert mock_manager.start_receiver.call_count == 2

    def test_start_while_live_same_device_is_idempotent(self, mock_manager, mock_player):
        svc = _make_service(mock_manager, mock_player)
        r1 = svc.start(correlation_id="cid-1", device_id="dev-1")
        r2 = svc.start(correlation_id="cid-2", device_id="dev-1")
        assert r1["success"] is True
        assert r2["success"] is True
        assert r2["status"]["active"] is True
        mock_manager.start_receiver.assert_called_once()

    def test_start_while_live_different_device_does_takeover(
        self, mock_manager, mock_player
    ):
        """Different device gets a takeover, not a rejection."""
        svc = _make_service(mock_manager, mock_player)
        r1 = svc.start(correlation_id="cid-1", device_id="dev-1")
        r2 = svc.start(correlation_id="cid-2", device_id="dev-2")
        assert r1["success"] is True
        assert r2["success"] is True
        assert r2.get("takeover") is True
        assert r2["status"]["active"] is True
        assert mock_manager.stop_receiver.call_count == 1
        assert mock_manager.start_receiver.call_count == 2

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

    def test_start_sets_heartbeat_timestamp(self, mock_manager, mock_player):
        """Fix 2: _last_heartbeat_at must be initialized on start to monitor everyone."""
        svc = _make_service(mock_manager, mock_player)
        svc.start(device_id="dev-1")
        assert svc._last_heartbeat_at > 0.0



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

    def test_stop_clears_mid_takeover_flag(self, mock_manager, mock_player):
        """stop() during a takeover cancels it."""
        svc = _make_service(mock_manager, mock_player)
        svc._mid_takeover = True
        svc.stop()
        assert svc._mid_takeover is False


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
            "owner_device_id",
        }

    def test_status_owner_device_id_is_none_when_idle(self, mock_manager, mock_player):
        svc = _make_service(mock_manager, mock_player)
        st = svc.status()
        assert st["owner_device_id"] is None

    def test_status_owner_device_id_reflects_active_session(
        self, mock_manager, mock_player
    ):
        mock_manager.is_alive.return_value = True
        svc = _make_service(mock_manager, mock_player)
        svc.start(device_id="dev-42")
        st = svc.status()
        assert st["owner_device_id"] == "dev-42"


# --------------- Takeover tests ---------------


class TestStreamServiceTakeover:
    def test_different_device_causes_takeover(self, mock_manager, mock_player):
        svc = _make_service(mock_manager, mock_player)
        r1 = svc.start(correlation_id="cid-1", device_id="dev-1")
        r2 = svc.start(correlation_id="cid-2", device_id="dev-2")
        assert r1["success"] is True
        assert r2["success"] is True
        assert r2.get("takeover") is True
        assert mock_manager.stop_receiver.call_count == 1
        assert mock_manager.start_receiver.call_count == 2

    def test_takeover_calls_wait_for_stop_complete(self, mock_manager, mock_player):
        """Takeover must call wait_for_stop_complete (outside service lock)."""
        svc = _make_service(mock_manager, mock_player)
        svc.start(device_id="dev-1")
        svc.start(device_id="dev-2")
        # Called twice: once in normal start (dev-1) and once in takeover (dev-2).
        assert mock_manager.wait_for_stop_complete.call_count == 2

    def test_takeover_preserves_source_before_stream(self, mock_manager, mock_player):
        mock_player.get_state.return_value = {
            "is_playing": True,
            "playlist": {"active": True},
        }
        svc = _make_service(mock_manager, mock_player)
        svc.start(device_id="dev-1")
        # Playlist already stopped by first start; second start sees idle player.
        mock_player.get_state.return_value = {
            "is_playing": False,
            "playlist": {"active": False},
        }
        r2 = svc.start(device_id="dev-2")
        assert r2["success"] is True
        assert r2["status"]["source_before_stream"] == "playlist"

    def test_takeover_without_device_id_succeeds(self, mock_manager, mock_player):
        svc = _make_service(mock_manager, mock_player)
        svc.start(device_id="dev-1")
        r2 = svc.start()  # no device_id — still takes over
        assert r2["success"] is True
        assert r2.get("takeover") is True
        assert mock_manager.stop_receiver.call_count == 1

    def test_same_device_is_idempotent_no_stop(self, mock_manager, mock_player):
        svc = _make_service(mock_manager, mock_player)
        svc.start(device_id="dev-1")
        r2 = svc.start(device_id="dev-1")
        assert r2["success"] is True
        mock_manager.stop_receiver.assert_not_called()
        mock_manager.start_receiver.assert_called_once()

    def test_takeover_updates_active_device_id(self, mock_manager, mock_player):
        mock_manager.is_alive.return_value = True
        svc = _make_service(mock_manager, mock_player)
        svc.start(device_id="dev-1")
        svc.start(device_id="dev-2")
        st = svc.status()
        assert st["owner_device_id"] == "dev-2"

    @patch("services.stream_service.StreamService._restore_playlist")
    def test_takeover_failure_restores_playlist(
        self, mock_restore, mock_manager, mock_player
    ):
        """Fix 1: takeover receiver failure must restore playlist to prevent silence."""
        mock_player.get_state.return_value = {
            "is_playing": True,
            "playlist": {"active": True},
        }
        svc = _make_service(mock_manager, mock_player)
        svc.start(device_id="dev-1")
        # Make the takeover's start_receiver call fail (phase 3).
        mock_manager.start_receiver.side_effect = None
        mock_manager.start_receiver.return_value = False
        r2 = svc.start(device_id="dev-2")
        assert r2["success"] is False
        assert r2["status"]["state"] == "error"
        mock_restore.assert_called_once()

    def test_takeover_receiver_start_failure_sets_error(
        self, mock_manager, mock_player
    ):
        svc = _make_service(mock_manager, mock_player)
        svc.start(device_id="dev-1")
        mock_manager.start_receiver.side_effect = None
        mock_manager.start_receiver.return_value = False
        r2 = svc.start(device_id="dev-2")
        assert r2["success"] is False
        assert r2["status"]["state"] == "error"
        assert r2["status"]["last_error"] == "receiver_start_failed"

    def test_concurrent_start_during_takeover_is_rejected(
        self, mock_manager, mock_player
    ):
        """While _mid_takeover is True, a concurrent start returns an error."""
        svc = _make_service(mock_manager, mock_player)
        svc.start(device_id="dev-1")
        # Simulate phase 2: mid-takeover flag set
        svc._mid_takeover = True
        r = svc.start(device_id="dev-3")
        assert r["success"] is False
        assert r["error"] == "takeover_in_progress"

    def test_pause_or_policy_during_takeover_aborts_takeover(
        self, mock_manager, mock_player
    ):
        """Fix 3 flaw: pause/policy_stop during Phase 2 wait must clear _mid_takeover."""
        svc = _make_service(mock_manager, mock_player)
        svc.start(device_id="dev-1")
        svc._mid_takeover = True
        
        # Someone calls pause_for_announcement while takeover is waiting
        svc.pause_for_announcement()
        
        # Then Phase 3 resumes. If _mid_takeover wasn't cleared, it proceeds and overwrites state!
        assert svc._mid_takeover is False, "pause_for_announcement must clear _mid_takeover"
        
        svc._mid_takeover = True
        svc.force_stop_by_policy()
        assert svc._mid_takeover is False, "force_stop_by_policy must clear _mid_takeover"


# --------------- Heartbeat unit tests ---------------


class TestStreamServiceHeartbeat:
    def test_heartbeat_accepted_when_live(self, mock_manager, mock_player):
        mock_manager.is_alive.return_value = True
        svc = _make_service(mock_manager, mock_player)
        svc.start(device_id="dev-1")
        result = svc.heartbeat(device_id="dev-1")
        assert result["accepted"] is True

    def test_heartbeat_rejected_when_not_owner(self, mock_manager, mock_player):
        svc = _make_service(mock_manager, mock_player)
        svc.start(device_id="dev-1")
        result = svc.heartbeat(device_id="dev-2")
        assert result["accepted"] is False
        assert result["reason"] == "not_owner"
        assert result["owner_device_id"] == "dev-1"

    def test_heartbeat_rejected_when_no_stream(self, mock_manager, mock_player):
        svc = _make_service(mock_manager, mock_player)
        result = svc.heartbeat(device_id="dev-1")
        assert result["accepted"] is False
        assert result["reason"] == "no_active_stream"

    def test_heartbeat_accepted_when_no_device_tracking(
        self, mock_manager, mock_player
    ):
        """Stream started without device_id accepts heartbeats from anyone."""
        svc = _make_service(mock_manager, mock_player)
        svc.start()  # no device_id
        result = svc.heartbeat(device_id="dev-1")
        assert result["accepted"] is True

    def test_heartbeat_activates_monitoring(self, mock_manager, mock_player):
        """_last_heartbeat_at is refreshed appropriately on heartbeat."""
        svc = _make_service(mock_manager, mock_player)
        svc.start(device_id="dev-1")
        before = svc._last_heartbeat_at
        time.sleep(0.01)
        svc.heartbeat(device_id="dev-1")
        assert svc._last_heartbeat_at > before

    def test_heartbeat_expiry_auto_stops_stream(self, mock_manager, mock_player):
        svc = _make_service(mock_manager, mock_player)
        svc.start(device_id="dev-1")
        # Activate monitoring and immediately expire it.
        svc._last_heartbeat_at = time.monotonic() - (HEARTBEAT_TIMEOUT + 1)
        stopped = svc._check_heartbeat()
        assert stopped is True
        st = svc.status()
        assert st["active"] is False
        assert st["state"] == "idle"
        mock_manager.stop_receiver.assert_called_once()

    def test_heartbeat_expiry_stops_stream_without_device_id(
        self, mock_manager, mock_player
    ):
        """Fix 2: streams without device_id are also monitored after first HB."""
        svc = _make_service(mock_manager, mock_player)
        svc.start()  # no device_id
        svc._last_heartbeat_at = time.monotonic() - (HEARTBEAT_TIMEOUT + 1)
        stopped = svc._check_heartbeat()
        assert stopped is True
        mock_manager.stop_receiver.assert_called_once()

    def test_heartbeat_not_expired_does_not_stop(self, mock_manager, mock_player):
        svc = _make_service(mock_manager, mock_player)
        svc.start(device_id="dev-1")
        svc._last_heartbeat_at = time.monotonic() - 5  # 5 s, well within limit
        result = svc._check_heartbeat()
        assert result is False
        mock_manager.stop_receiver.assert_not_called()

    def test_stream_without_heartbeat_gets_monitored(self, mock_manager, mock_player):
        """Fix 2: Stream that never sends heartbeat is still monitored from start."""
        svc = _make_service(mock_manager, mock_player)
        svc.start(device_id="dev-1")
        assert svc._last_heartbeat_at > 0.0  # monitoring starts immediately
        # Simulate expiry
        svc._last_heartbeat_at = time.monotonic() - (HEARTBEAT_TIMEOUT + 1)
        result = svc._check_heartbeat()
        assert result is True
        mock_manager.stop_receiver.assert_called_once()

    def test_heartbeat_resets_on_stop(self, mock_manager, mock_player):
        svc = _make_service(mock_manager, mock_player)
        svc.start(device_id="dev-1")
        svc.heartbeat(device_id="dev-1")
        assert svc._last_heartbeat_at > 0.0
        svc.stop()
        assert svc._last_heartbeat_at == 0.0

    def test_heartbeat_accepted_while_paused_for_announcement(
        self, mock_manager, mock_player
    ):
        svc = _make_service(mock_manager, mock_player)
        svc.start(device_id="dev-1")
        svc.pause_for_announcement()
        result = svc.heartbeat(device_id="dev-1")
        assert result["accepted"] is True


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
        mock_svc.start.assert_called_once_with(
            correlation_id="cid-route-1",
            device_id=None,
        )

    @patch("routes.stream_routes._stream_service")
    def test_start_endpoint_forwards_device_header(self, mock_svc, client):
        mock_svc.start.return_value = {
            "success": True,
            "status": StreamStatus(active=True, state="live").to_dict(),
        }
        resp = client.post(
            "/api/stream/start",
            headers={"X-Stream-Device-Id": "dev-route-1"},
        )
        assert resp.status_code == 200
        mock_svc.start.assert_called_once_with(
            correlation_id=None,
            device_id="dev-route-1",
        )

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
        status = StreamStatus().to_dict()
        status["owner_device_id"] = None
        mock_svc.status.return_value = status
        resp = client.get("/api/stream/status")
        assert resp.status_code == 200
        data = resp.get_json()
        assert set(data.keys()) == {
            "active",
            "state",
            "source_before_stream",
            "last_error",
            "owner_device_id",
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

    @patch("routes.stream_routes._stream_service")
    def test_start_already_live_returns_409(self, mock_svc, client):
        mock_svc.start.return_value = {
            "success": False,
            "error": "stream_already_live",
            "status": StreamStatus(active=True, state="live").to_dict(),
        }
        resp = client.post("/api/stream/start")
        assert resp.status_code == 409


# --------------- Heartbeat route tests ---------------


class TestHeartbeatRoute:
    @patch("routes.stream_routes._stream_service")
    def test_heartbeat_accepted_returns_200(self, mock_svc, client):
        mock_svc.heartbeat.return_value = {
            "accepted": True,
            "status": StreamStatus(active=True, state="live").to_dict(),
        }
        resp = client.post(
            "/api/stream/heartbeat",
            headers={"X-Stream-Device-Id": "dev-1"},
        )
        assert resp.status_code == 200
        mock_svc.heartbeat.assert_called_once_with(device_id="dev-1")

    @patch("routes.stream_routes._stream_service")
    def test_heartbeat_not_owner_returns_409(self, mock_svc, client):
        mock_svc.heartbeat.return_value = {
            "accepted": False,
            "reason": "not_owner",
            "owner_device_id": "dev-1",
            "status": StreamStatus().to_dict(),
        }
        resp = client.post(
            "/api/stream/heartbeat",
            headers={"X-Stream-Device-Id": "dev-2"},
        )
        assert resp.status_code == 409

    @patch("routes.stream_routes._stream_service")
    def test_heartbeat_no_stream_returns_400(self, mock_svc, client):
        mock_svc.heartbeat.return_value = {
            "accepted": False,
            "reason": "no_active_stream",
            "status": StreamStatus().to_dict(),
        }
        resp = client.post("/api/stream/heartbeat")
        assert resp.status_code == 400

    def test_heartbeat_requires_login(self):
        """Heartbeat endpoint must reject unauthenticated requests."""
        app.config["TESTING"] = True
        c = app.test_client()
        resp = c.post("/api/stream/heartbeat", follow_redirects=False)
        assert resp.status_code in (301, 302)
        assert "/login" in resp.headers.get("Location", "")

    @patch("routes.stream_routes._stream_service")
    def test_heartbeat_forwards_device_header(self, mock_svc, client):
        mock_svc.heartbeat.return_value = {
            "accepted": True,
            "status": StreamStatus(active=True, state="live").to_dict(),
        }
        client.post(
            "/api/stream/heartbeat",
            headers={"X-Stream-Device-Id": "dev-xyz"},
        )
        mock_svc.heartbeat.assert_called_once_with(device_id="dev-xyz")

    @patch("routes.stream_routes._stream_service")
    def test_heartbeat_without_device_header_passes_none(self, mock_svc, client):
        mock_svc.heartbeat.return_value = {
            "accepted": True,
            "status": StreamStatus(active=True, state="live").to_dict(),
        }
        client.post("/api/stream/heartbeat")
        mock_svc.heartbeat.assert_called_once_with(device_id=None)


# --------------- Playlist stream guard ---------------


class TestPlaylistStreamGuard:
    """Playlist endpoints must reject playback when stream is active."""

    @patch("routes.playlist_routes.get_stream_service")
    def test_set_blocked_when_stream_live(self, mock_get_svc, client):
        """Fix 4: /set must also be guarded."""
        mock_svc = MagicMock()
        mock_svc.status.return_value = {"active": True, "state": "live"}
        mock_get_svc.return_value = mock_svc
        resp = client.post(
            "/api/playlist/set",
            json={"media_ids": [1, 2]},
        )
        assert resp.status_code == 409

    @patch("routes.playlist_routes.get_stream_service")
    def test_start_all_blocked_when_stream_live(self, mock_get_svc, client):
        mock_svc = MagicMock()
        mock_svc.status.return_value = {"active": True, "state": "live"}
        mock_get_svc.return_value = mock_svc
        resp = client.post("/api/playlist/start-all")
        assert resp.status_code == 409

    @patch("routes.playlist_routes.get_stream_service")
    def test_play_blocked_when_stream_live(self, mock_get_svc, client):
        mock_svc = MagicMock()
        mock_svc.status.return_value = {"active": True, "state": "live"}
        mock_get_svc.return_value = mock_svc
        resp = client.post("/api/playlist/play")
        assert resp.status_code == 409

    @patch("routes.playlist_routes.get_stream_service")
    def test_start_all_allowed_when_stream_idle(self, mock_get_svc, client):
        mock_svc = MagicMock()
        mock_svc.status.return_value = {"active": False, "state": "idle"}
        mock_get_svc.return_value = mock_svc
        # Will fail with 404 (no music files) but should NOT be 409
        resp = client.post("/api/playlist/start-all")
        assert resp.status_code != 409

    @patch("routes.playlist_routes.get_stream_service")
    def test_set_allowed_when_stream_idle(self, mock_get_svc, client):
        mock_svc = MagicMock()
        mock_svc.status.return_value = {"active": False, "state": "idle"}
        mock_get_svc.return_value = mock_svc
        # Will fail with 400/404 (no body) but NOT 409
        resp = client.post("/api/playlist/set", json={})
        assert resp.status_code != 409


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
