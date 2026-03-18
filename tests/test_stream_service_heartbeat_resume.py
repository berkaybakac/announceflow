"""Regression tests for Bug 2: heartbeat timer reset on announcement resume.

Root cause (observed 2026-03-18 in production):
  resume_after_announcement() restored state to "live" without resetting
  _last_heartbeat_at, so if ≥15 s had elapsed since the last heartbeat
  (which arrived just before the pause), _check_heartbeat() fired immediately
  on the next 5-second monitor tick and killed the live stream.

Fix: reset _last_heartbeat_at = time.monotonic() on resume when monitoring
is active (_last_heartbeat_at > 0), giving the agent a fresh 15-second window.
"""
import time
from unittest.mock import MagicMock

import pytest

from services.stream_service import HEARTBEAT_TIMEOUT, StreamService
from stream_manager import StreamManager


# --------------- helpers ---------------


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


# --------------- tests ---------------


class TestHeartbeatResumeRace:
    def test_resume_resets_heartbeat_timer(self, mock_manager, mock_player):
        """After pause+resume the timer must be at least as recent as resume time.

        Without the fix, _last_heartbeat_at keeps the pre-pause value, so a
        long announcement (>= 15 s) causes immediate stream kill on resume.
        """
        svc = _make_service(mock_manager, mock_player)
        svc.start(device_id="dev-1")
        svc.heartbeat(device_id="dev-1")

        old_ts = svc._last_heartbeat_at
        time.sleep(0.01)

        svc.pause_for_announcement()
        time.sleep(0.02)
        svc.resume_after_announcement()

        assert svc._last_heartbeat_at > old_ts, (
            "resume_after_announcement must refresh _last_heartbeat_at "
            "so the agent gets a fresh window"
        )

    def test_resume_does_not_activate_monitoring_when_disabled(
        self, mock_manager, mock_player
    ):
        """Panel starts (no device_id) have monitoring disabled (_last_heartbeat_at == 0).

        resume_after_announcement must NOT turn monitoring on for these sessions.
        """
        svc = _make_service(mock_manager, mock_player)
        svc.start()  # no device_id → monitoring dormant
        assert svc._last_heartbeat_at == 0.0

        svc.pause_for_announcement()
        svc.resume_after_announcement()

        assert svc._last_heartbeat_at == 0.0, (
            "Monitoring must stay dormant after resume for panel-started streams"
        )

    def test_no_false_expiry_after_long_announcement(self, mock_manager, mock_player):
        """Production scenario: heartbeat arrived 14 s before a long announcement.

        After resume _check_heartbeat() must NOT auto-stop the stream.
        Simulated by backdating _last_heartbeat_at before pause.
        """
        svc = _make_service(mock_manager, mock_player)
        svc.start(device_id="dev-1")

        # Simulate last heartbeat arriving 14 s ago
        svc._last_heartbeat_at = time.monotonic() - 14.0

        svc.pause_for_announcement()
        # Announcement takes 2 s → total since last HB would be ~16 s without fix
        time.sleep(0.01)
        svc.resume_after_announcement()

        stopped = svc._check_heartbeat()
        assert stopped is False, (
            "Stream must survive after resume when last heartbeat was within "
            "the window at the time of the pause"
        )
        assert svc._status.state == "live"
        mock_manager.stop_receiver.assert_called_once()  # only from pause, not expiry

    def test_heartbeat_expiry_still_works_after_resume(self, mock_manager, mock_player):
        """Sanity: if the agent truly goes silent after resume, expiry fires normally."""
        svc = _make_service(mock_manager, mock_player)
        svc.start(device_id="dev-1")

        svc.pause_for_announcement()
        svc.resume_after_announcement()

        # Simulate 16 s of silence after resume
        svc._last_heartbeat_at = time.monotonic() - (HEARTBEAT_TIMEOUT + 1)
        stopped = svc._check_heartbeat()
        assert stopped is True
