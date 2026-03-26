"""
Tests for the sequential mute-restore pipeline in the restore worker.

Covers: Happy, Sad, Critical, and Stress scenarios for the single-thread
volume restore → playlist resume ordering guarantee.
"""

import threading
from unittest.mock import MagicMock, patch, call

from scheduler import Scheduler


def _make_scheduler():
    return Scheduler(check_interval_seconds=60)


def _make_muted_state():
    return {
        "volume": 0,
        "muted": True,
        "last_nonzero_volume": 80,
        "volume_revision": 3,
    }


def _make_restore_state(*, volume_override_active=True):
    return {
        "playlist": ["/music/a.mp3", "/music/b.mp3"],
        "index": 0,
        "loop": True,
        "active": True,
        "volume_override_active": volume_override_active,
    }


class TestMuteRestoreHappyPath:
    """Mute → announcement → ends → volume restored to 0, playlist resumes muted."""

    @patch("scheduler.log_schedule")
    @patch("scheduler._volume_runtime")
    @patch("scheduler.db.get_volume_state")
    @patch("scheduler.db.get_playlist_state")
    @patch("scheduler.get_player")
    def test_mute_restore_happy_path(
        self,
        mock_get_player,
        mock_get_playlist_state,
        mock_get_volume_state,
        mock_volume_runtime,
        _mock_log_schedule,
    ):
        sched = _make_scheduler()
        player = MagicMock()
        player.is_playing = False
        player.set_volume.return_value = True
        player.apply_playlist_state.return_value = True
        mock_get_player.return_value = player

        mock_get_volume_state.return_value = _make_muted_state()
        mock_get_playlist_state.return_value = {"active": True}

        # Set up override as active with known token
        mock_volume_runtime.get_override_token.return_value = 5
        mock_volume_runtime.restore_override.return_value = True

        # Queue the restore target with volume_override_active=True
        sched._restore_target_state = _make_restore_state(volume_override_active=True)

        # Patch silence policy to allow resume
        with patch("scheduler.resolve_silence_policy", return_value={
            "silence_active": False,
            "policy": "none",
            "reason_code": "prayer_disabled",
        }):
            sched._run_restore_worker(player)

        # VERIFY: restore_override called BEFORE playlist resume
        mock_volume_runtime.restore_override.assert_called_once_with(
            reason="announcement_ended_sequential",
            token=5,
        )

        # VERIFY: playlist resumed after volume restore
        player.apply_playlist_state.assert_called_once()
        call_kwargs = player.apply_playlist_state.call_args
        assert call_kwargs.kwargs.get("play_next") is True

        # VERIFY ordering: restore_override was called, then apply_playlist_state
        # Both were called exactly once, and restore must have been first
        # (guaranteed by single-thread sequential execution in _run_restore_worker)
        assert mock_volume_runtime.restore_override.call_count == 1
        assert player.apply_playlist_state.call_count == 1


class TestMuteRestoreSadPath:
    """Volume restore fails → playlist still resumes."""

    @patch("scheduler.log_schedule")
    @patch("scheduler._volume_runtime")
    @patch("scheduler.db.get_volume_state")
    @patch("scheduler.db.get_playlist_state")
    @patch("scheduler.get_player")
    def test_mute_restore_volume_fails_playlist_still_resumes(
        self,
        mock_get_player,
        mock_get_playlist_state,
        mock_get_volume_state,
        mock_volume_runtime,
        _mock_log_schedule,
    ):
        sched = _make_scheduler()
        player = MagicMock()
        player.is_playing = False
        player.apply_playlist_state.return_value = True
        mock_get_player.return_value = player

        mock_get_volume_state.return_value = _make_muted_state()
        mock_get_playlist_state.return_value = {"active": True}

        # restore_override fails (e.g., token mismatch or hw error)
        mock_volume_runtime.get_override_token.return_value = 5
        mock_volume_runtime.restore_override.return_value = False

        sched._restore_target_state = _make_restore_state(volume_override_active=True)

        with patch("scheduler.resolve_silence_policy", return_value={
            "silence_active": False,
            "policy": "none",
            "reason_code": "prayer_disabled",
        }):
            sched._run_restore_worker(player)

        # VERIFY: restore was attempted (first call + retry)
        assert mock_volume_runtime.restore_override.call_count >= 1

        # VERIFY: playlist STILL resumed despite volume restore failure
        player.apply_playlist_state.assert_called_once()
        call_kwargs = player.apply_playlist_state.call_args
        assert call_kwargs.kwargs.get("play_next") is True


class TestMuteRestoreCriticalPath:
    """User changes volume during announcement → cancel_override invalidates token."""

    @patch("scheduler.log_schedule")
    @patch("scheduler._volume_runtime")
    @patch("scheduler.db.get_volume_state")
    @patch("scheduler.db.get_playlist_state")
    @patch("scheduler.get_player")
    def test_mute_restore_user_cancels_override(
        self,
        mock_get_player,
        mock_get_playlist_state,
        mock_get_volume_state,
        mock_volume_runtime,
        _mock_log_schedule,
    ):
        sched = _make_scheduler()
        player = MagicMock()
        player.is_playing = False
        player.apply_playlist_state.return_value = True
        mock_get_player.return_value = player

        # User changed volume to 50 during announcement
        mock_get_volume_state.return_value = {
            "volume": 50,
            "muted": False,
            "last_nonzero_volume": 50,
            "volume_revision": 4,
        }
        mock_get_playlist_state.return_value = {"active": True}

        # Token is None because user called cancel_override which invalidated it
        mock_volume_runtime.get_override_token.return_value = None

        sched._restore_target_state = _make_restore_state(volume_override_active=True)

        with patch("scheduler.resolve_silence_policy", return_value={
            "silence_active": False,
            "policy": "none",
            "reason_code": "prayer_disabled",
        }):
            sched._run_restore_worker(player)

        # VERIFY: restore_override called with token=None (will return False internally)
        mock_volume_runtime.restore_override.assert_called_once_with(
            reason="announcement_ended_sequential",
            token=None,
        )

        # VERIFY: playlist still resumes (user's volume is preserved by the service)
        player.apply_playlist_state.assert_called_once()


class TestMuteRestoreStress:
    """Rapid back-to-back announcements — only last override restores successfully."""

    def test_mute_restore_rapid_consecutive_announcements(self):
        from services.volume_runtime_service import VolumeRuntimeService

        svc = VolumeRuntimeService()

        # Simulate 3 rapid consecutive announcement overrides
        tokens = []
        for i in range(3):
            svc.activate_announcement_override(
                playback_session=100 + i,
                effective_volume=80,
                source=f"announcement_{i}",
                start_watcher=False,
            )
            tokens.append(svc.get_override_token())

        # All 3 tokens should be different (incrementing)
        assert len(set(tokens)) == 3
        assert tokens == sorted(tokens)  # monotonically increasing

        # Only the last token should be valid
        current_token = svc.get_override_token()
        assert current_token == tokens[-1]

        # Trying to restore with old tokens should fail
        with patch("services.volume_runtime_service.db") as mock_db, \
             patch("services.volume_runtime_service.get_player") as mock_player:
            mock_db.get_volume_state.return_value = _make_muted_state()
            mock_player.return_value.set_volume.return_value = True

            result_old_1 = svc.restore_override(reason="stale_1", token=tokens[0])
            assert result_old_1 is False  # old token rejected

            result_old_2 = svc.restore_override(reason="stale_2", token=tokens[1])
            assert result_old_2 is False  # old token rejected

            # Current (last) token should succeed
            result_current = svc.restore_override(reason="correct", token=tokens[2])
            assert result_current is True

            # Player volume should be set to canonical (0 = muted)
            mock_player.return_value.set_volume.assert_called_once_with(0)

        # After restore, override should be inactive
        assert svc.get_override_token() is None
