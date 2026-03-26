"""Announcement mute override + silence-policy integration tests."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from scheduler import Scheduler
from web_panel import app


@pytest.fixture
def client():
    app.config["TESTING"] = True
    with app.test_client() as c:
        with c.session_transaction() as sess:
            sess["logged_in"] = True
        yield c


class TestManualAnnouncementPolicy:
    @patch("routes.player_routes._reject_if_outside_working_hours", return_value=None)
    @patch("routes.player_routes._get_media_or_404")
    @patch("routes.player_routes.resolve_silence_policy")
    @patch("routes.player_routes.get_player")
    def test_manual_announcement_blocked_when_silence_active(
        self,
        mock_get_player,
        mock_resolve_policy,
        mock_get_media,
        _mock_hours_guard,
        client,
    ):
        player = MagicMock()
        player.play.return_value = True
        mock_get_player.return_value = player
        mock_get_media.return_value = (
            {"id": 1, "filename": "azan.mp3", "filepath": "/tmp/azan.mp3", "media_type": "announcement"},
            None,
        )
        mock_resolve_policy.return_value = {
            "silence_active": True,
            "policy": "prayer",
            "reason_code": "prayer_window_active",
        }

        resp = client.post("/api/play", json={"media_id": 1})
        data = resp.get_json()

        assert resp.status_code == 403
        assert "error" in data
        player.play.assert_not_called()

    @patch("routes.player_routes._volume_runtime")
    @patch("routes.player_routes.db.update_playback_state")
    @patch("routes.player_routes.db.get_volume_state")
    @patch("routes.player_routes._reject_if_outside_working_hours", return_value=None)
    @patch("routes.player_routes._get_media_or_404")
    @patch("routes.player_routes.resolve_silence_policy")
    @patch("routes.player_routes.get_player")
    def test_manual_announcement_applies_runtime_override_when_muted(
        self,
        mock_get_player,
        mock_resolve_policy,
        mock_get_media,
        _mock_hours_guard,
        mock_get_volume_state,
        _mock_update_state,
        mock_volume_runtime,
        client,
    ):
        player = MagicMock()
        player._playlist_active = False
        player._playlist = []
        player.play.return_value = True
        player.set_volume.return_value = True
        player._playback_session = 77
        mock_get_player.return_value = player
        mock_get_media.return_value = (
            {"id": 1, "filename": "duyuru.mp3", "filepath": "/tmp/duyuru.mp3", "media_type": "announcement"},
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
            "volume_revision": 12,
        }

        resp = client.post("/api/play", json={"media_id": 1})
        data = resp.get_json()

        assert resp.status_code == 200
        assert data["success"] is True
        player.set_volume.assert_called_with(40)
        mock_volume_runtime.activate_announcement_override.assert_called_once_with(
            playback_session=77,
            effective_volume=40,
            source="manual_announcement",
        )


class TestScheduledAnnouncementPolicy:
    @patch("scheduler._volume_runtime")
    @patch("scheduler.db.get_volume_state")
    @patch("scheduler.resolve_silence_policy")
    @patch("scheduler.is_within_working_hours")
    @patch("scheduler.get_stream_service")
    @patch("scheduler.get_player")
    def test_scheduled_announcement_applies_runtime_override_when_muted(
        self,
        mock_get_player,
        mock_get_stream_service,
        mock_is_working_hours,
        mock_resolve_policy,
        mock_get_volume_state,
        mock_volume_runtime,
    ):
        scheduler = Scheduler(check_interval_seconds=1)
        player = MagicMock()
        player.is_playing = False
        player.set_volume.return_value = True
        player._playback_session = 91
        mock_get_player.return_value = player

        stream_service = MagicMock()
        stream_service.status.return_value = {"active": False}
        mock_get_stream_service.return_value = stream_service
        mock_is_working_hours.return_value = True
        mock_resolve_policy.return_value = {
            "silence_active": False,
            "policy": "none",
            "reason_code": "prayer_disabled",
        }
        mock_get_volume_state.return_value = {
            "volume": 0,
            "muted": True,
            "last_nonzero_volume": 35,
            "volume_revision": 5,
        }

        with patch.object(
            scheduler,
            "_capture_restore_snapshot",
            return_value={
                "playlist_was_active": False,
                "playlist_files": [],
                "playlist_index": -1,
                "playlist_loop": True,
            },
        ), patch.object(scheduler, "_interrupt_for_scheduled_media"), patch.object(
            scheduler, "_start_scheduled_media", return_value=True
        ), patch.object(
            scheduler, "_queue_restore_target", return_value=False
        ):
            scheduler._play_media(
                filepath="/tmp/announce.mp3",
                schedule_id=10,
                is_one_time=False,
                is_announcement=True,
            )

        player.set_volume.assert_called_with(35)
        mock_volume_runtime.activate_announcement_override.assert_called_once_with(
            playback_session=91,
            effective_volume=35,
            source="scheduled_announcement",
        )

    @patch("scheduler.db.update_one_time_schedule_status")
    @patch("scheduler.log_schedule")
    @patch("scheduler.resolve_silence_policy")
    @patch("scheduler.is_within_working_hours")
    @patch("scheduler.get_stream_service")
    @patch("scheduler.get_player")
    def test_scheduled_announcement_not_cancelled_when_silence_active(
        self,
        mock_get_player,
        mock_get_stream_service,
        mock_is_working_hours,
        mock_resolve_policy,
        mock_log_schedule,
        mock_update_one_time_status,
    ):
        scheduler = Scheduler(check_interval_seconds=1)
        mock_get_player.return_value = MagicMock()
        mock_get_stream_service.return_value = MagicMock()
        mock_is_working_hours.return_value = True
        mock_resolve_policy.return_value = {
            "silence_active": True,
            "policy": "prayer",
            "reason_code": "prayer_window_active",
        }

        with patch.object(scheduler, "_start_scheduled_media") as mock_start:
            result = scheduler._play_media(
                filepath="/tmp/announce.mp3",
                schedule_id=55,
                is_one_time=True,
                is_announcement=True,
            )

        assert result is False
        mock_start.assert_not_called()
        mock_update_one_time_status.assert_not_called()
        mock_log_schedule.assert_called_once()
        event, payload = mock_log_schedule.call_args.args
        assert event == "scheduled_media_blocked_policy"
        assert payload["schedule_id"] == 55
        assert payload["is_one_time"] is True
        assert payload["is_announcement"] is True
        assert payload["policy"] == "prayer"
        assert payload["reason_code"] == "prayer_window_active"

    @patch("scheduler.log_error")
    @patch("scheduler._volume_runtime")
    @patch("scheduler.db.get_volume_state")
    @patch("scheduler.resolve_silence_policy")
    @patch("scheduler.is_within_working_hours")
    @patch("scheduler.get_stream_service")
    @patch("scheduler.get_player")
    def test_scheduled_override_apply_failure_logs_error(
        self,
        mock_get_player,
        mock_get_stream_service,
        mock_is_working_hours,
        mock_resolve_policy,
        mock_get_volume_state,
        _mock_volume_runtime,
        mock_log_error,
    ):
        scheduler = Scheduler(check_interval_seconds=1)
        player = MagicMock()
        player.is_playing = False
        player.set_volume.return_value = False
        mock_get_player.return_value = player

        stream_service = MagicMock()
        stream_service.status.return_value = {"active": False}
        mock_get_stream_service.return_value = stream_service
        mock_is_working_hours.return_value = True
        mock_resolve_policy.return_value = {
            "silence_active": False,
            "policy": "none",
            "reason_code": "prayer_disabled",
        }
        mock_get_volume_state.return_value = {
            "volume": 0,
            "muted": True,
            "last_nonzero_volume": 33,
            "volume_revision": 9,
        }

        with patch.object(
            scheduler,
            "_capture_restore_snapshot",
            return_value={
                "playlist_was_active": False,
                "playlist_files": [],
                "playlist_index": -1,
                "playlist_loop": True,
            },
        ), patch.object(scheduler, "_interrupt_for_scheduled_media"), patch.object(
            scheduler, "_start_scheduled_media", return_value=True
        ), patch.object(
            scheduler, "_queue_restore_target", return_value=False
        ):
            scheduler._play_media(
                filepath="/tmp/announce.mp3",
                schedule_id=77,
                is_one_time=False,
                is_announcement=True,
            )

        mock_log_error.assert_called_once()
        event, payload = mock_log_error.call_args.args
        assert event == "scheduled_override_volume_apply_failed"
        assert payload["schedule_id"] == 77
        assert payload["override_volume"] == 33
        assert payload["canonical_volume"] == 0
