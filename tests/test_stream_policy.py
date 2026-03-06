"""Phase 4 stream policy and runtime behavior tests."""

from unittest.mock import MagicMock

import pytest

from services.stream_policy import (
    should_force_stop_stream,
    should_interrupt_for_announcement,
    should_resume_stream,
    should_skip_scheduled_music,
)
from services.stream_service import StreamService


@pytest.fixture
def mock_manager():
    mgr = MagicMock()
    mgr.start_receiver.return_value = True
    mgr.stop_receiver.return_value = True
    mgr.is_alive.return_value = True
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
    return StreamService(stream_manager=mock_manager, player_fn=lambda: mock_player)


@pytest.mark.parametrize(
    ("stream_active", "silence_active", "silence_ended", "sender_alive"),
    [
        (True, True, True, True),
        (True, False, True, False),
        (False, True, False, True),
        (False, False, False, False),
    ],
)
def test_stream_policy_bool_rules(
    stream_active: bool,
    silence_active: bool,
    silence_ended: bool,
    sender_alive: bool,
):
    assert should_interrupt_for_announcement(stream_active) is stream_active
    assert should_skip_scheduled_music(stream_active) is stream_active
    assert should_force_stop_stream(silence_active) is silence_active
    assert should_resume_stream(silence_ended, sender_alive) is (
        silence_ended and sender_alive
    )


def test_sender_alive_uses_policy_resume_armed_and_not_user_stopped(
    mock_manager, mock_player
):
    svc = _make_service(mock_manager, mock_player)
    assert svc.policy_sender_alive() is False

    svc.start()
    assert svc.policy_sender_alive() is True

    svc.stop()
    assert svc.policy_sender_alive() is False
    mock_manager.is_alive.assert_not_called()


def test_status_receiver_died_check_runs_only_in_live_state(mock_manager, mock_player):
    svc = _make_service(mock_manager, mock_player)
    svc.start()
    svc.pause_for_announcement()
    mock_manager.is_alive.return_value = False

    state = svc.status()
    assert state["state"] == "paused_for_announcement"
    assert state["last_error"] is None
    mock_manager.is_alive.assert_not_called()


def test_pause_for_announcement_is_intentional_non_error(mock_manager, mock_player):
    svc = _make_service(mock_manager, mock_player)
    svc.start()

    result = svc.pause_for_announcement()
    assert result["success"] is True
    assert result["status"]["active"] is True
    assert result["status"]["state"] == "paused_for_announcement"
    assert result["status"]["last_error"] is None


def test_force_stop_by_policy_is_intentional_non_error(mock_manager, mock_player):
    svc = _make_service(mock_manager, mock_player)
    svc.start()

    result = svc.force_stop_by_policy()
    assert result["success"] is True
    assert result["status"]["active"] is False
    assert result["status"]["state"] == "stopped_by_policy"
    assert result["status"]["last_error"] is None


def test_user_stop_clears_policy_resume_arm(mock_manager, mock_player):
    svc = _make_service(mock_manager, mock_player)
    svc.start()
    svc.force_stop_by_policy()
    assert svc.policy_sender_alive() is True

    svc.stop()
    assert svc.policy_sender_alive() is False


def test_resume_after_announcement_receiver_fail(mock_manager, mock_player):
    """P2: receiver restart fails after announcement → error state."""
    svc = _make_service(mock_manager, mock_player)
    svc.start()
    svc.pause_for_announcement()

    mock_manager.start_receiver.return_value = False
    result = svc.resume_after_announcement()
    assert result["success"] is False
    assert result["status"]["state"] == "error"
    assert result["status"]["last_error"] == "receiver_start_failed"
    assert result["status"]["active"] is False


def test_resume_after_policy_receiver_fail(mock_manager, mock_player):
    """P2: receiver restart fails after silence-policy end → error state."""
    svc = _make_service(mock_manager, mock_player)
    svc.start()
    svc.force_stop_by_policy()

    mock_manager.start_receiver.return_value = False
    result = svc.resume_after_policy()
    assert result["success"] is False
    assert result["status"]["state"] == "error"
    assert result["status"]["last_error"] == "receiver_start_failed"
    assert result["status"]["active"] is False


def test_resume_after_policy_reuses_existing_correlation_id(mock_manager, mock_player):
    svc = _make_service(mock_manager, mock_player)
    svc.start(correlation_id="cid-policy-1")
    svc.force_stop_by_policy()

    result = svc.resume_after_policy()
    assert result["success"] is True
    assert result["status"]["state"] == "live"
    assert mock_manager.start_receiver.call_count >= 2
    assert mock_manager.start_receiver.call_args_list[-1].kwargs == {
        "correlation_id": "cid-policy-1"
    }


def test_resume_after_announcement_when_sender_dead(mock_manager, mock_player):
    """P1: If sender becomes dead during announcement, resume drops to idle instead of sticking to paused."""
    svc = _make_service(mock_manager, mock_player)
    svc.start()
    svc.pause_for_announcement()
    
    # Simulate sender dying without calling stop()
    svc._policy_resume_armed = False
    
    result = svc.resume_after_announcement()
    assert result["success"] is True
    assert result["status"]["state"] == "idle"
    assert result["status"]["active"] is False
