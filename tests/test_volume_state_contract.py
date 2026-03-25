"""Canonical volume state contract tests for /api/volume and /api/now-playing."""
from unittest.mock import MagicMock, patch

import pytest

from web_panel import app


@pytest.fixture
def client():
    app.config["TESTING"] = True
    with app.test_client() as c:
        with c.session_transaction() as sess:
            sess["logged_in"] = True
        yield c


@pytest.fixture
def anon_client():
    """Unauthenticated client for auth guard tests."""
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


def _mock_player(prev_volume: int = 30, set_ok: bool = True):
    player = MagicMock()
    player.get_volume.return_value = prev_volume
    player.set_volume.return_value = set_ok
    player.get_state.return_value = {"is_playing": False}
    return player


class TestCanonicalVolumeRoute:
    @patch("routes.player_routes._volume_runtime")
    @patch("routes.player_routes.db.set_volume_state")
    @patch("routes.player_routes.db.get_volume_state")
    @patch("routes.player_routes.get_player")
    def test_set_absolute_volume_returns_canonical_state(
        self,
        mock_get_player,
        mock_get_volume_state,
        mock_set_volume_state,
        mock_volume_runtime,
        client,
    ):
        mock_get_player.return_value = _mock_player(prev_volume=30, set_ok=True)
        mock_volume_runtime.get_effective_state.return_value = {
            "effective_volume": 55,
            "effective_muted": False,
            "mute_override_active": False,
        }
        mock_get_volume_state.return_value = {
            "volume": 30,
            "muted": False,
            "last_nonzero_volume": 30,
            "volume_revision": 7,
        }
        mock_set_volume_state.return_value = {
            "volume": 55,
            "muted": False,
            "last_nonzero_volume": 55,
            "volume_revision": 8,
        }

        resp = client.post("/api/volume", json={"volume": 55})
        data = resp.get_json()

        assert resp.status_code == 200
        assert data["success"] is True
        assert data["volume"] == 55
        assert data["muted"] is False
        assert data["last_nonzero_volume"] == 55
        assert data["volume_revision"] == 8
        assert data["effective_volume"] == 55
        assert data["effective_muted"] is False
        assert data["mute_override_active"] is False
        mock_volume_runtime.cancel_override.assert_called_once_with(
            reason="user_volume_intent",
            restore=False,
        )
        mock_get_player.return_value.set_volume.assert_called_once_with(55)
        mock_set_volume_state.assert_called_once_with(55)

    @patch("routes.player_routes._volume_runtime")
    @patch("routes.player_routes.db.set_volume_state")
    @patch("routes.player_routes.db.get_volume_state")
    @patch("routes.player_routes.get_player")
    def test_mute_true_sets_volume_zero_keeps_last_nonzero(
        self,
        mock_get_player,
        mock_get_volume_state,
        mock_set_volume_state,
        mock_volume_runtime,
        client,
    ):
        mock_get_player.return_value = _mock_player(prev_volume=40, set_ok=True)
        mock_volume_runtime.get_effective_state.return_value = {
            "effective_volume": 0,
            "effective_muted": True,
            "mute_override_active": False,
        }
        mock_get_volume_state.return_value = {
            "volume": 40,
            "muted": False,
            "last_nonzero_volume": 40,
            "volume_revision": 9,
        }
        mock_set_volume_state.return_value = {
            "volume": 0,
            "muted": True,
            "last_nonzero_volume": 40,
            "volume_revision": 10,
        }

        resp = client.post("/api/volume", json={"muted": True})
        data = resp.get_json()

        assert resp.status_code == 200
        assert data["success"] is True
        assert data["volume"] == 0
        assert data["muted"] is True
        assert data["last_nonzero_volume"] == 40
        assert data["effective_volume"] == 0
        assert data["effective_muted"] is True
        assert data["mute_override_active"] is False
        mock_get_player.return_value.set_volume.assert_called_once_with(0)
        mock_set_volume_state.assert_called_once_with(0)

    @patch("routes.player_routes._volume_runtime")
    @patch("routes.player_routes.db.set_volume_state")
    @patch("routes.player_routes.db.get_volume_state")
    @patch("routes.player_routes.get_player")
    def test_unmute_restores_last_nonzero(
        self,
        mock_get_player,
        mock_get_volume_state,
        mock_set_volume_state,
        mock_volume_runtime,
        client,
    ):
        mock_get_player.return_value = _mock_player(prev_volume=0, set_ok=True)
        mock_volume_runtime.get_effective_state.return_value = {
            "effective_volume": 40,
            "effective_muted": False,
            "mute_override_active": False,
        }
        mock_get_volume_state.return_value = {
            "volume": 0,
            "muted": True,
            "last_nonzero_volume": 40,
            "volume_revision": 10,
        }
        mock_set_volume_state.return_value = {
            "volume": 40,
            "muted": False,
            "last_nonzero_volume": 40,
            "volume_revision": 11,
        }

        resp = client.post("/api/volume", json={"muted": False})
        data = resp.get_json()

        assert resp.status_code == 200
        assert data["success"] is True
        assert data["volume"] == 40
        assert data["muted"] is False
        assert data["last_nonzero_volume"] == 40
        assert data["effective_volume"] == 40
        assert data["effective_muted"] is False
        assert data["mute_override_active"] is False
        mock_get_player.return_value.set_volume.assert_called_once_with(40)
        mock_set_volume_state.assert_called_once_with(40)

    @patch("routes.player_routes._volume_runtime")
    @patch("routes.player_routes.db.get_volume_state")
    @patch("routes.player_routes.get_player")
    def test_user_volume_write_cancels_runtime_override(
        self, mock_get_player, mock_get_volume_state, mock_volume_runtime, client
    ):
        mock_get_player.return_value = _mock_player(prev_volume=20, set_ok=False)
        mock_get_volume_state.return_value = {
            "volume": 20,
            "muted": False,
            "last_nonzero_volume": 20,
            "volume_revision": 1,
        }
        mock_volume_runtime.get_effective_state.return_value = {
            "effective_volume": 20,
            "effective_muted": False,
            "mute_override_active": False,
        }

        resp = client.post("/api/volume", json={"volume": 30})
        assert resp.status_code == 200
        mock_volume_runtime.cancel_override.assert_called_once_with(
            reason="user_volume_intent",
            restore=False,
        )

    def test_invalid_payload_with_volume_and_muted_returns_400(self, client):
        resp = client.post("/api/volume", json={"volume": 30, "muted": True})
        assert resp.status_code == 400
        assert "error" in resp.get_json()


class TestNowPlayingCanonicalFields:
    @patch("routes.player_routes._volume_runtime")
    @patch("routes.player_routes.db.get_media_by_filename")
    @patch("routes.player_routes.db.get_volume_state")
    @patch("routes.player_routes.get_player")
    def test_now_playing_exposes_canonical_volume_fields(
        self,
        mock_get_player,
        mock_get_volume_state,
        mock_get_media,
        mock_volume_runtime,
        client,
    ):
        player = _mock_player(prev_volume=20, set_ok=True)
        player.get_state.return_value = {"is_playing": False, "filename": None}
        mock_get_player.return_value = player
        mock_volume_runtime.get_effective_state.return_value = {
            "effective_volume": 35,
            "effective_muted": False,
            "mute_override_active": True,
        }
        mock_get_volume_state.return_value = {
            "volume": 0,
            "muted": True,
            "last_nonzero_volume": 35,
            "volume_revision": 12,
        }
        mock_get_media.return_value = None

        resp = client.get("/api/now-playing")
        data = resp.get_json()

        assert resp.status_code == 200
        assert data["volume"] == 0
        assert data["muted"] is True
        assert data["last_nonzero_volume"] == 35
        assert data["volume_revision"] == 12
        assert data["effective_volume"] == 35
        assert data["effective_muted"] is False
        assert data["mute_override_active"] is True


# ── Auth guard tests ──────────────────────────────────────────────────────


class TestVolumeAuthGuard:
    def test_volume_unauthenticated_returns_redirect(self, anon_client):
        resp = anon_client.post("/api/volume", json={"volume": 50})
        assert resp.status_code in (302, 401)

    def test_now_playing_unauthenticated_returns_redirect(self, anon_client):
        resp = anon_client.get("/api/now-playing")
        assert resp.status_code in (302, 401)


class TestPlayAuthGuard:
    def test_play_unauthenticated_returns_redirect(self, anon_client):
        resp = anon_client.post("/api/play", json={"filename": "test.mp3"})
        assert resp.status_code in (302, 401)


# ── Invalid payload branch tests ──────────────────────────────────────────


class TestVolumeInvalidPayloads:
    def test_empty_body_returns_400(self, client):
        resp = client.post("/api/volume", json={})
        assert resp.status_code == 400

    def test_string_volume_returns_400(self, client):
        resp = client.post("/api/volume", json={"volume": "abc"})
        assert resp.status_code == 400

    def test_negative_volume_returns_400(self, client):
        resp = client.post("/api/volume", json={"volume": -5})
        assert resp.status_code == 400

    def test_volume_above_100_returns_400(self, client):
        resp = client.post("/api/volume", json={"volume": 150})
        assert resp.status_code == 400

    def test_muted_non_bool_returns_400(self, client):
        resp = client.post("/api/volume", json={"muted": "yes"})
        assert resp.status_code == 400

    def test_neither_volume_nor_muted_returns_400(self, client):
        resp = client.post("/api/volume", json={"foo": "bar"})
        assert resp.status_code == 400


# ── Override restore failure rollback ─────────────────────────────────────


class TestOverrideRestoreFailure:
    @patch("services.volume_runtime_service.get_player")
    @patch("services.volume_runtime_service.db")
    def test_restore_override_returns_false_on_player_failure(
        self, mock_db, mock_get_player
    ):
        from services.volume_runtime_service import VolumeRuntimeService

        svc = VolumeRuntimeService()
        mock_db.get_volume_state.return_value = {
            "volume": 50,
            "muted": False,
            "last_nonzero_volume": 50,
            "volume_revision": 1,
        }
        mock_get_player.return_value.set_volume.side_effect = OSError("hw fail")

        # Activate an override first
        svc.activate_announcement_override(
            playback_session=1,
            effective_volume=80,
            source="test",
        )

        result = svc.restore_override(reason="test_done")
        assert result is False
