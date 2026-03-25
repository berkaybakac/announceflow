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

    @patch("routes.player_routes.log_error")
    @patch("routes.player_routes._volume_runtime")
    @patch("routes.player_routes.db.get_volume_state")
    @patch("routes.player_routes.get_player")
    def test_volume_apply_failure_logs_error_branch(
        self,
        mock_get_player,
        mock_get_volume_state,
        mock_volume_runtime,
        mock_log_error,
        client,
    ):
        mock_get_player.return_value = _mock_player(prev_volume=22, set_ok=False)
        mock_get_volume_state.return_value = {
            "volume": 22,
            "muted": False,
            "last_nonzero_volume": 22,
            "volume_revision": 5,
        }
        mock_volume_runtime.get_effective_state.return_value = {
            "effective_volume": 22,
            "effective_muted": False,
            "mute_override_active": False,
        }

        resp = client.post("/api/volume", json={"muted": True})
        data = resp.get_json()

        assert resp.status_code == 200
        assert data["success"] is False
        mock_log_error.assert_called_once()
        event, payload = mock_log_error.call_args.args
        assert event == "volume_apply_failed"
        assert payload["target_volume"] == 0
        assert payload["current_volume"] == 22
        assert payload["muted_intent"] is True
        assert payload["revision"] == 5
        assert payload["override_active"] is False

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


class TestPlayLogging:
    @patch("routes.player_routes.log_error")
    @patch("routes.player_routes.log_web")
    @patch("routes.player_routes._reject_if_outside_working_hours", return_value=None)
    @patch("routes.player_routes._get_media_or_404")
    @patch("routes.player_routes.resolve_silence_policy")
    @patch("routes.player_routes.get_player")
    def test_manual_announcement_blocked_logs_policy_events(
        self,
        mock_get_player,
        mock_resolve_policy,
        mock_get_media,
        _mock_hours_guard,
        mock_log_web,
        mock_log_error,
        client,
    ):
        mock_get_player.return_value = MagicMock()
        mock_get_media.return_value = (
            {
                "id": 1,
                "filename": "ezan.mp3",
                "filepath": "/tmp/ezan.mp3",
                "media_type": "announcement",
            },
            None,
        )
        mock_resolve_policy.return_value = {
            "silence_active": True,
            "policy": "prayer",
            "reason_code": "prayer_window_active",
        }

        resp = client.post("/api/play", json={"media_id": 1})
        assert resp.status_code == 403

        mock_log_web.assert_called_once()
        mock_log_error.assert_called_once()
        web_event, web_payload = mock_log_web.call_args.args
        err_event, err_payload = mock_log_error.call_args.args
        assert web_event == "play_blocked_policy"
        assert err_event == "play_blocked_policy"
        assert web_payload["media_id"] == 1
        assert web_payload["policy"] == "prayer"
        assert err_payload["reason_code"] == "prayer_window_active"

    @patch("routes.player_routes.log_error")
    @patch("routes.player_routes.db.get_volume_state")
    @patch("routes.player_routes._reject_if_outside_working_hours", return_value=None)
    @patch("routes.player_routes._get_media_or_404")
    @patch("routes.player_routes.resolve_silence_policy")
    @patch("routes.player_routes.get_player")
    def test_manual_play_failure_logs_and_rolls_back_override_failure(
        self,
        mock_get_player,
        mock_resolve_policy,
        mock_get_media,
        _mock_hours_guard,
        mock_get_volume_state,
        mock_log_error,
        client,
    ):
        player = MagicMock()
        player._playlist_active = False
        player._playlist = []
        player.play.return_value = False
        player.set_volume.side_effect = [True, False]
        mock_get_player.return_value = player
        mock_get_media.return_value = (
            {
                "id": 2,
                "filename": "duyuru.mp3",
                "filepath": "/tmp/duyuru.mp3",
                "media_type": "announcement",
            },
            None,
        )
        mock_resolve_policy.return_value = {
            "silence_active": False,
            "policy": "none",
            "reason_code": "prayer_disabled",
        }
        mock_get_volume_state.return_value = {
            "volume": 0,
            "muted": True,
            "last_nonzero_volume": 40,
            "volume_revision": 2,
        }

        resp = client.post("/api/play", json={"media_id": 2})
        data = resp.get_json()

        assert resp.status_code == 200
        assert data["success"] is False
        events = [call.args[0] for call in mock_log_error.call_args_list]
        assert "play_failed" in events
        assert "override_rollback_failed" in events

        rollback_payload = next(
            call.args[1]
            for call in mock_log_error.call_args_list
            if call.args and call.args[0] == "override_rollback_failed"
        )
        assert rollback_payload["canonical_volume"] == 0
        assert rollback_payload["media_id"] == 2


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
    @patch("services.volume_runtime_service.VolumeRuntimeService._start_session_watcher")
    @patch("services.volume_runtime_service.log_error")
    @patch("services.volume_runtime_service.get_player")
    @patch("services.volume_runtime_service.db")
    def test_restore_override_returns_false_on_player_failure(
        self, mock_db, mock_get_player, mock_log_error, _mock_start_watcher
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
        mock_log_error.assert_called_once()
        event, payload = mock_log_error.call_args.args
        assert event == "volume_override_restore_failed"
        assert payload["reason"] == "test_done"
        assert payload["source"] == "test"
        assert payload["canonical_volume"] == 50
