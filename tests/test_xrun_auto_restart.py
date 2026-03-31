"""Tests for xrun auto-restart mechanism.

When the receiver subprocess accumulates too many ALSA xruns within a
rolling window, StreamService should automatically restart it.  This is
throttled to a maximum number of restarts per hour.
"""
import time
from unittest.mock import MagicMock, patch

import pytest

from services.stream_service import (
    StreamService,
    XRUN_RESTART_THRESHOLD,
    XRUN_RESTART_WINDOW_SECONDS,
    XRUN_MAX_RESTARTS_PER_HOUR,
    XRUN_AUTO_RESTART_COOLDOWN_SECONDS,
)
from stream_manager import StreamManager


# --------------- fixtures ---------------


@pytest.fixture
def mock_manager():
    mgr = MagicMock(spec=StreamManager)
    mgr.start_receiver.return_value = True
    mgr.stop_receiver.return_value = True
    mgr.is_alive.return_value = True
    mgr.wait_for_stop_complete.return_value = None
    mgr.read_xrun_status.return_value = None
    return mgr


@pytest.fixture(autouse=True)
def _disable_xrun_dry_run(monkeypatch):
    """Keep legacy restart tests stable unless test explicitly enables dry-run."""
    monkeypatch.setenv("ANNOUNCEFLOW_XRUN_AUTO_RECOVERY_DRY_RUN", "false")


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


def _make_live_service(mock_manager, mock_player, correlation_id="test-cid"):
    """Create a StreamService with an active live stream."""
    svc = StreamService(
        stream_manager=mock_manager, player_fn=lambda: mock_player
    )
    # Simulate an active live session.
    svc._status.active = True
    svc._status.state = "live"
    svc._active_correlation_id = correlation_id
    return svc


def _make_started_service(mock_manager, mock_player, device_id="test-dev"):
    svc = StreamService(stream_manager=mock_manager, player_fn=lambda: mock_player)
    result = svc.start(device_id=device_id)
    assert result["success"] is True
    return svc


def _capture_xrun_events(monkeypatch):
    events = []

    def _log_system(name, payload):
        if str(name).startswith("stream_xrun_auto_restart"):
            events.append((name, payload))

    monkeypatch.setattr("services.stream_service.log_system", _log_system)
    return events


def _assert_xrun_event_payload_shape(payload):
    required = {
        "correlation_id",
        "xruns_in_window",
        "total_xruns",
        "restarts_this_hour",
        "state",
        "active",
        "reason",
        "dry_run",
        "threshold",
        "window_seconds",
        "xrun_peak_1s",
        "xrun_peak_60s",
        "xrun_max_consecutive",
        "xrun_current_consecutive",
        "xrun_session_rate_per_sec",
        "xrun_burst_rate_per_sec",
    }
    assert required.issubset(set(payload.keys()))


# --------------- tests ---------------


class TestXrunAutoRestart:
    def test_no_restart_when_below_threshold(self, mock_manager, mock_player):
        """Xrun count below threshold should not trigger restart."""
        svc = _make_live_service(mock_manager, mock_player)

        # First call: initializes window.
        mock_manager.read_xrun_status.return_value = {
            "alsa_xrun": 10,
            "udp_overrun": 0,
            "mono_ts": time.monotonic(),
            "correlation_id": "test-cid",
        }
        assert svc._check_xrun_auto_restart() is False

        # Second call: still below threshold.
        mock_manager.read_xrun_status.return_value = {
            "alsa_xrun": 50,
            "udp_overrun": 0,
            "mono_ts": time.monotonic(),
            "correlation_id": "test-cid",
        }
        assert svc._check_xrun_auto_restart() is False
        mock_manager.stop_receiver.assert_not_called()

    def test_restart_when_threshold_exceeded(self, mock_manager, mock_player, monkeypatch):
        """Xrun count exceeding threshold within window should trigger restart."""
        events = _capture_xrun_events(monkeypatch)
        svc = _make_live_service(mock_manager, mock_player)

        # Initialize window.
        mock_manager.read_xrun_status.return_value = {
            "alsa_xrun": 10,
            "udp_overrun": 0,
            "mono_ts": time.monotonic(),
            "correlation_id": "test-cid",
        }
        svc._check_xrun_auto_restart()

        # Exceed threshold.
        mock_manager.read_xrun_status.return_value = {
            "alsa_xrun": 10 + XRUN_RESTART_THRESHOLD,
            "udp_overrun": 0,
            "mono_ts": time.monotonic(),
            "correlation_id": "test-cid",
        }
        result = svc._check_xrun_auto_restart()
        assert result is True
        mock_manager.stop_receiver.assert_called_once()
        mock_manager.start_receiver.assert_called_once_with(
            correlation_id="test-cid", wait_for_stop=True,
        )
        assert len(svc._xrun_auto_restart_times) == 1
        assert [name for name, _ in events] == ["stream_xrun_auto_restart"]
        payload = events[0][1]
        _assert_xrun_event_payload_shape(payload)
        assert payload["reason"] == "threshold_exceeded"
        assert payload["restarts_this_hour"] == 1

    def test_dry_run_emits_alarm_without_restart(
        self, mock_manager, mock_player, monkeypatch
    ):
        monkeypatch.setenv("ANNOUNCEFLOW_XRUN_AUTO_RECOVERY_DRY_RUN", "true")
        monkeypatch.setenv("ANNOUNCEFLOW_XRUN_RESTART_THRESHOLD", "5")
        events = _capture_xrun_events(monkeypatch)
        svc = _make_live_service(mock_manager, mock_player)

        mock_manager.read_xrun_status.return_value = {
            "alsa_xrun": 10,
            "udp_overrun": 0,
            "mono_ts": time.monotonic(),
            "correlation_id": "test-cid",
            "xrun_peak_1s": 4,
            "xrun_peak_60s": 12,
            "xrun_max_consecutive": 8,
            "xrun_current_consecutive": 3,
            "xrun_session_rate_per_sec": 1.25,
            "xrun_burst_rate_per_sec": 9.5,
        }
        assert svc._check_xrun_auto_restart() is False

        mock_manager.read_xrun_status.return_value = {
            "alsa_xrun": 15,
            "udp_overrun": 0,
            "mono_ts": time.monotonic(),
            "correlation_id": "test-cid",
            "xrun_peak_1s": 7,
            "xrun_peak_60s": 21,
            "xrun_max_consecutive": 10,
            "xrun_current_consecutive": 5,
            "xrun_session_rate_per_sec": 2.0,
            "xrun_burst_rate_per_sec": 11.2,
        }
        result = svc._check_xrun_auto_restart()
        assert result is False
        mock_manager.stop_receiver.assert_not_called()
        mock_manager.start_receiver.assert_not_called()
        mock_manager.wait_for_stop_complete.assert_not_called()
        assert [name for name, _ in events] == ["stream_xrun_auto_restart_dry_run"]
        payload = events[0][1]
        _assert_xrun_event_payload_shape(payload)
        assert payload["reason"] == "threshold_exceeded_dry_run"
        assert payload["dry_run"] is True
        assert payload["threshold"] == 5
        assert payload["xrun_peak_1s"] == 7
        assert payload["xrun_peak_60s"] == 21
        assert payload["xrun_max_consecutive"] == 10
        assert payload["xrun_current_consecutive"] == 5
        assert payload["xrun_session_rate_per_sec"] == 2.0
        assert payload["xrun_burst_rate_per_sec"] == 11.2

    def test_invalid_env_values_fall_back_to_safe_defaults(
        self, mock_manager, mock_player, monkeypatch
    ):
        monkeypatch.setenv("ANNOUNCEFLOW_XRUN_AUTO_RECOVERY_DRY_RUN", "false")
        monkeypatch.setenv("ANNOUNCEFLOW_XRUN_RESTART_THRESHOLD", "bad")
        monkeypatch.setenv("ANNOUNCEFLOW_XRUN_RESTART_WINDOW_SECONDS", "-5")
        events = _capture_xrun_events(monkeypatch)
        svc = _make_live_service(mock_manager, mock_player)

        mock_manager.read_xrun_status.return_value = {
            "alsa_xrun": 10,
            "udp_overrun": 0,
            "mono_ts": time.monotonic(),
            "correlation_id": "test-cid",
        }
        assert svc._check_xrun_auto_restart() is False

        mock_manager.read_xrun_status.return_value = {
            "alsa_xrun": 10 + XRUN_RESTART_THRESHOLD,
            "udp_overrun": 0,
            "mono_ts": time.monotonic(),
            "correlation_id": "test-cid",
        }
        assert svc._check_xrun_auto_restart() is True
        assert [name for name, _ in events] == ["stream_xrun_auto_restart"]
        payload = events[0][1]
        _assert_xrun_event_payload_shape(payload)
        assert payload["dry_run"] is False
        assert payload["threshold"] == XRUN_RESTART_THRESHOLD
        assert payload["window_seconds"] == XRUN_RESTART_WINDOW_SECONDS

    def test_no_restart_when_stream_not_live(self, mock_manager, mock_player):
        """Should not check xrun when stream is not active/live."""
        svc = StreamService(
            stream_manager=mock_manager, player_fn=lambda: mock_player
        )
        # Default: not active, idle state.
        mock_manager.read_xrun_status.return_value = {
            "alsa_xrun": 500,
            "udp_overrun": 0,
            "mono_ts": time.monotonic(),
            "correlation_id": "test-cid",
        }
        assert svc._check_xrun_auto_restart() is False
        mock_manager.stop_receiver.assert_not_called()

    def test_throttle_limits_restarts_per_hour(self, mock_manager, mock_player, monkeypatch):
        """Should not exceed max restarts per hour."""
        events = _capture_xrun_events(monkeypatch)
        svc = _make_live_service(mock_manager, mock_player)

        # Fill up restart budget.
        svc._xrun_auto_restart_times = [
            time.monotonic() - i * 60
            for i in range(XRUN_MAX_RESTARTS_PER_HOUR)
        ]

        # Initialize then exceed threshold.
        mock_manager.read_xrun_status.return_value = {
            "alsa_xrun": 10,
            "udp_overrun": 0,
            "mono_ts": time.monotonic(),
            "correlation_id": "test-cid",
        }
        svc._check_xrun_auto_restart()

        mock_manager.read_xrun_status.return_value = {
            "alsa_xrun": 10 + XRUN_RESTART_THRESHOLD,
            "udp_overrun": 0,
            "mono_ts": time.monotonic(),
            "correlation_id": "test-cid",
        }
        result = svc._check_xrun_auto_restart()
        assert result is False, "Should be throttled by max restarts/hour"
        mock_manager.stop_receiver.assert_not_called()
        assert len(svc._xrun_auto_restart_times) == XRUN_MAX_RESTARTS_PER_HOUR
        assert [name for name, _ in events] == [
            "stream_xrun_auto_restart_skipped_throttled"
        ]
        payload = events[0][1]
        _assert_xrun_event_payload_shape(payload)
        assert payload["reason"] == "restart_budget_exhausted"

    def test_skips_restart_when_cooldown_active(
        self, mock_manager, mock_player, monkeypatch
    ):
        """After a successful restart, cooldown should suppress new attempts."""
        events = _capture_xrun_events(monkeypatch)
        svc = _make_live_service(mock_manager, mock_player)

        # First successful auto-restart.
        mock_manager.read_xrun_status.return_value = {
            "alsa_xrun": 10,
            "udp_overrun": 0,
            "mono_ts": time.monotonic(),
            "correlation_id": "test-cid",
        }
        assert svc._check_xrun_auto_restart() is False
        mock_manager.read_xrun_status.return_value = {
            "alsa_xrun": 10 + XRUN_RESTART_THRESHOLD,
            "udp_overrun": 0,
            "mono_ts": time.monotonic(),
            "correlation_id": "test-cid",
        }
        assert svc._check_xrun_auto_restart() is True
        baseline_restarts = len(svc._xrun_auto_restart_times)
        assert baseline_restarts == 1

        mock_manager.stop_receiver.reset_mock()
        mock_manager.start_receiver.reset_mock()
        mock_manager.wait_for_stop_complete.reset_mock()

        # New threshold crossing while cooldown is still active.
        mock_manager.read_xrun_status.return_value = {
            "alsa_xrun": 200,
            "udp_overrun": 0,
            "mono_ts": time.monotonic(),
            "correlation_id": "test-cid",
        }
        assert svc._check_xrun_auto_restart() is False
        mock_manager.read_xrun_status.return_value = {
            "alsa_xrun": 200 + XRUN_RESTART_THRESHOLD,
            "udp_overrun": 0,
            "mono_ts": time.monotonic(),
            "correlation_id": "test-cid",
        }
        assert svc._check_xrun_auto_restart() is False

        mock_manager.stop_receiver.assert_not_called()
        mock_manager.start_receiver.assert_not_called()
        mock_manager.wait_for_stop_complete.assert_not_called()
        assert len(svc._xrun_auto_restart_times) == baseline_restarts
        assert events[-1][0] == "stream_xrun_auto_restart_skipped_cooldown"
        _assert_xrun_event_payload_shape(events[-1][1])
        assert events[-1][1]["reason"] == "cooldown_active"

    def test_restart_allowed_after_cooldown_expires(
        self, mock_manager, mock_player, monkeypatch
    ):
        """Cooldown expiry should allow a new restart attempt."""
        events = _capture_xrun_events(monkeypatch)
        svc = _make_live_service(mock_manager, mock_player)

        # First successful auto-restart.
        mock_manager.read_xrun_status.return_value = {
            "alsa_xrun": 10,
            "udp_overrun": 0,
            "mono_ts": time.monotonic(),
            "correlation_id": "test-cid",
        }
        assert svc._check_xrun_auto_restart() is False
        mock_manager.read_xrun_status.return_value = {
            "alsa_xrun": 10 + XRUN_RESTART_THRESHOLD,
            "udp_overrun": 0,
            "mono_ts": time.monotonic(),
            "correlation_id": "test-cid",
        }
        assert svc._check_xrun_auto_restart() is True
        assert len(svc._xrun_auto_restart_times) == 1

        # Force cooldown expiry and trigger again.
        svc._xrun_restart_cooldown_until_mono = (
            time.monotonic() - XRUN_AUTO_RESTART_COOLDOWN_SECONDS
        )
        mock_manager.stop_receiver.reset_mock()
        mock_manager.start_receiver.reset_mock()
        mock_manager.wait_for_stop_complete.reset_mock()
        mock_manager.read_xrun_status.return_value = {
            "alsa_xrun": 400,
            "udp_overrun": 0,
            "mono_ts": time.monotonic(),
            "correlation_id": "test-cid",
        }
        assert svc._check_xrun_auto_restart() is False
        mock_manager.read_xrun_status.return_value = {
            "alsa_xrun": 400 + XRUN_RESTART_THRESHOLD,
            "udp_overrun": 0,
            "mono_ts": time.monotonic(),
            "correlation_id": "test-cid",
        }
        assert svc._check_xrun_auto_restart() is True

        mock_manager.stop_receiver.assert_called_once()
        mock_manager.start_receiver.assert_called_once()
        mock_manager.wait_for_stop_complete.assert_called_once()
        assert len(svc._xrun_auto_restart_times) == 2
        assert [name for name, _ in events].count("stream_xrun_auto_restart") == 2

    def test_cooldown_skip_does_not_consume_restart_budget(
        self, mock_manager, mock_player, monkeypatch
    ):
        """Cooldown skip should keep restart budget unchanged."""
        events = _capture_xrun_events(monkeypatch)
        svc = _make_live_service(mock_manager, mock_player)

        # Prime window and set an active cooldown while budget is still available.
        mock_manager.read_xrun_status.return_value = {
            "alsa_xrun": 10,
            "udp_overrun": 0,
            "mono_ts": time.monotonic(),
            "correlation_id": "test-cid",
        }
        assert svc._check_xrun_auto_restart() is False
        svc._xrun_auto_restart_times = [
            time.monotonic() - 60 * i for i in range(XRUN_MAX_RESTARTS_PER_HOUR - 1)
        ]
        svc._xrun_restart_cooldown_until_mono = time.monotonic() + 30.0
        baseline_restarts = len(svc._xrun_auto_restart_times)

        mock_manager.read_xrun_status.return_value = {
            "alsa_xrun": 10 + XRUN_RESTART_THRESHOLD,
            "udp_overrun": 0,
            "mono_ts": time.monotonic(),
            "correlation_id": "test-cid",
        }
        assert svc._check_xrun_auto_restart() is False

        assert len(svc._xrun_auto_restart_times) == baseline_restarts
        assert events[-1][0] == "stream_xrun_auto_restart_skipped_cooldown"
        assert events[-1][1]["reason"] == "cooldown_active"
        mock_manager.stop_receiver.assert_not_called()
        mock_manager.start_receiver.assert_not_called()

    def test_window_slides_after_expiry(self, mock_manager, mock_player, monkeypatch):
        """After window expires, a new window starts with current count."""
        _capture_xrun_events(monkeypatch)
        svc = _make_live_service(mock_manager, mock_player)

        # Initialize window with high count.
        mock_manager.read_xrun_status.return_value = {
            "alsa_xrun": 500,
            "udp_overrun": 0,
            "mono_ts": time.monotonic(),
            "correlation_id": "test-cid",
        }
        svc._check_xrun_auto_restart()

        # Simulate window expiry by backdating.
        svc._xrun_window_start_mono = time.monotonic() - XRUN_RESTART_WINDOW_SECONDS - 1

        # Only 50 more xruns — below threshold from new window start.
        mock_manager.read_xrun_status.return_value = {
            "alsa_xrun": 550,
            "udp_overrun": 0,
            "mono_ts": time.monotonic(),
            "correlation_id": "test-cid",
        }
        result = svc._check_xrun_auto_restart()
        assert result is False
        mock_manager.stop_receiver.assert_not_called()

    def test_reset_tracking_on_stop(self, mock_manager, mock_player, monkeypatch):
        """Xrun tracking state should reset when stream stops."""
        _capture_xrun_events(monkeypatch)
        monkeypatch.setattr("services.stream_service.log_error", lambda *a, **kw: None)
        svc = _make_live_service(mock_manager, mock_player)

        svc._xrun_window_start_mono = 123.0
        svc._xrun_window_start_count = 50
        svc._xrun_last_known_count = 75
        svc._xrun_restart_cooldown_until_mono = time.monotonic() + 30.0

        svc.stop()

        assert svc._xrun_window_start_mono == 0.0
        assert svc._xrun_window_start_count == 0
        assert svc._xrun_last_known_count == 0
        assert svc._xrun_restart_cooldown_until_mono == 0.0

    def test_no_restart_when_no_status_file(self, mock_manager, mock_player):
        """Should gracefully handle missing xrun status file."""
        svc = _make_live_service(mock_manager, mock_player)
        mock_manager.read_xrun_status.return_value = None
        assert svc._check_xrun_auto_restart() is False

    def test_ignores_stale_correlation_id(self, mock_manager, mock_player):
        """Should ignore xrun status from a different session."""
        svc = _make_live_service(mock_manager, mock_player, correlation_id="new-cid")
        mock_manager.read_xrun_status.return_value = {
            "alsa_xrun": 500,
            "udp_overrun": 0,
            "mono_ts": time.monotonic(),
            "correlation_id": "old-cid",
        }
        assert svc._check_xrun_auto_restart() is False

    def test_abort_if_stopped_between_decision_and_restart(
        self, mock_manager, mock_player, monkeypatch
    ):
        """P1: If stop() runs between threshold check and restart, abort."""
        events = _capture_xrun_events(monkeypatch)
        svc = _make_live_service(mock_manager, mock_player)

        # Initialize window.
        mock_manager.read_xrun_status.return_value = {
            "alsa_xrun": 10,
            "udp_overrun": 0,
            "mono_ts": time.monotonic(),
            "correlation_id": "test-cid",
        }
        svc._check_xrun_auto_restart()

        # Exceed threshold.
        mock_manager.read_xrun_status.return_value = {
            "alsa_xrun": 10 + XRUN_RESTART_THRESHOLD,
            "udp_overrun": 0,
            "mono_ts": time.monotonic(),
            "correlation_id": "test-cid",
        }
        baseline_restarts = len(svc._xrun_auto_restart_times)

        def _stop_and_flip_state():
            svc._status.active = False
            svc._status.state = "idle"
            svc._active_correlation_id = None
            svc._user_stopped = True
            return True

        mock_manager.stop_receiver.side_effect = _stop_and_flip_state

        result = svc._check_xrun_auto_restart()
        assert result is False
        mock_manager.stop_receiver.assert_called_once()
        mock_manager.start_receiver.assert_not_called()
        assert len(svc._xrun_auto_restart_times) == baseline_restarts
        assert [name for name, _ in events] == ["stream_xrun_auto_restart_aborted"]
        _assert_xrun_event_payload_shape(events[0][1])
        assert events[0][1]["reason"] == "user_stopped"

    def test_reset_on_start(self, mock_manager, mock_player, monkeypatch):
        """P2: Xrun tracking resets when a new stream session starts."""
        _capture_xrun_events(monkeypatch)
        monkeypatch.setattr("services.stream_service.log_error", lambda *a, **kw: None)
        svc = StreamService(
            stream_manager=mock_manager, player_fn=lambda: mock_player
        )
        # Simulate leftover tracking from previous session.
        svc._xrun_window_start_mono = 100.0
        svc._xrun_window_start_count = 50
        svc._xrun_last_known_count = 75
        svc._xrun_restart_cooldown_until_mono = time.monotonic() + 30.0

        mock_manager.is_alive.return_value = False
        svc.start(device_id="dev1")

        assert svc._xrun_window_start_mono == 0.0
        assert svc._xrun_window_start_count == 0
        assert svc._xrun_last_known_count == 0
        assert svc._xrun_restart_cooldown_until_mono == 0.0

    def test_abort_when_intent_superseded(self, mock_manager, mock_player, monkeypatch):
        """If intent changes before restart phase, restart must abort."""
        events = _capture_xrun_events(monkeypatch)
        svc = _make_live_service(mock_manager, mock_player)

        mock_manager.read_xrun_status.return_value = {
            "alsa_xrun": 10,
            "udp_overrun": 0,
            "mono_ts": time.monotonic(),
            "correlation_id": "test-cid",
        }
        svc._check_xrun_auto_restart()

        mock_manager.read_xrun_status.return_value = {
            "alsa_xrun": 10 + XRUN_RESTART_THRESHOLD,
            "udp_overrun": 0,
            "mono_ts": time.monotonic(),
            "correlation_id": "test-cid",
        }

        def _supersede_intent():
            svc._xrun_restart_intent_id = "intent-other"
            return None

        mock_manager.wait_for_stop_complete.side_effect = _supersede_intent

        result = svc._check_xrun_auto_restart()
        assert result is False
        mock_manager.stop_receiver.assert_called_once()
        mock_manager.start_receiver.assert_not_called()
        assert [name for name, _ in events] == ["stream_xrun_auto_restart_aborted"]
        _assert_xrun_event_payload_shape(events[0][1])
        assert events[0][1]["reason"] == "intent_superseded"
        assert len(svc._xrun_auto_restart_times) == 0

    def test_failed_stop_emits_failed_event_and_does_not_consume_budget(
        self, mock_manager, mock_player, monkeypatch
    ):
        events = _capture_xrun_events(monkeypatch)
        svc = _make_live_service(mock_manager, mock_player)

        mock_manager.read_xrun_status.return_value = {
            "alsa_xrun": 10,
            "udp_overrun": 0,
            "mono_ts": time.monotonic(),
            "correlation_id": "test-cid",
        }
        svc._check_xrun_auto_restart()
        mock_manager.read_xrun_status.return_value = {
            "alsa_xrun": 10 + XRUN_RESTART_THRESHOLD,
            "udp_overrun": 0,
            "mono_ts": time.monotonic(),
            "correlation_id": "test-cid",
        }
        mock_manager.stop_receiver.return_value = False

        result = svc._check_xrun_auto_restart()
        assert result is False
        mock_manager.stop_receiver.assert_called_once()
        mock_manager.wait_for_stop_complete.assert_not_called()
        mock_manager.start_receiver.assert_not_called()
        assert [name for name, _ in events] == ["stream_xrun_auto_restart_failed"]
        _assert_xrun_event_payload_shape(events[0][1])
        assert events[0][1]["reason"] == "stop_receiver_failed"
        assert len(svc._xrun_auto_restart_times) == 0

    def test_failed_start_emits_failed_event_and_does_not_consume_budget(
        self, mock_manager, mock_player, monkeypatch
    ):
        events = _capture_xrun_events(monkeypatch)
        svc = _make_live_service(mock_manager, mock_player)

        mock_manager.read_xrun_status.return_value = {
            "alsa_xrun": 10,
            "udp_overrun": 0,
            "mono_ts": time.monotonic(),
            "correlation_id": "test-cid",
        }
        svc._check_xrun_auto_restart()
        mock_manager.read_xrun_status.return_value = {
            "alsa_xrun": 10 + XRUN_RESTART_THRESHOLD,
            "udp_overrun": 0,
            "mono_ts": time.monotonic(),
            "correlation_id": "test-cid",
        }
        mock_manager.start_receiver.return_value = False

        result = svc._check_xrun_auto_restart()
        assert result is False
        mock_manager.stop_receiver.assert_called_once()
        mock_manager.wait_for_stop_complete.assert_called_once()
        mock_manager.start_receiver.assert_called_once()
        assert [name for name, _ in events] == ["stream_xrun_auto_restart_failed"]
        _assert_xrun_event_payload_shape(events[0][1])
        assert events[0][1]["reason"] == "start_receiver_failed"
        assert len(svc._xrun_auto_restart_times) == 0

    def test_reset_on_takeover(self, mock_manager, mock_player, monkeypatch):
        """P2: tracking resets when takeover starts a new receiver session."""
        _capture_xrun_events(monkeypatch)
        svc = _make_started_service(mock_manager, mock_player, device_id="dev-1")
        svc._xrun_window_start_mono = 99.0
        svc._xrun_window_start_count = 7
        svc._xrun_last_known_count = 11
        svc._xrun_restart_cooldown_until_mono = time.monotonic() + 30.0

        result = svc.start(device_id="dev-2")
        assert result["success"] is True
        assert result.get("takeover") is True
        assert svc._xrun_window_start_mono == 0.0
        assert svc._xrun_window_start_count == 0
        assert svc._xrun_last_known_count == 0
        assert svc._xrun_restart_cooldown_until_mono == 0.0

    def test_reset_on_resume_after_announcement(
        self, mock_manager, mock_player, monkeypatch
    ):
        """P2: tracking resets on resume_after_announcement success."""
        _capture_xrun_events(monkeypatch)
        svc = _make_started_service(mock_manager, mock_player, device_id="dev-1")
        svc.pause_for_announcement()
        svc._xrun_window_start_mono = 77.0
        svc._xrun_window_start_count = 8
        svc._xrun_last_known_count = 13
        svc._xrun_restart_cooldown_until_mono = time.monotonic() + 30.0

        result = svc.resume_after_announcement()
        assert result["success"] is True
        assert svc._xrun_window_start_mono == 0.0
        assert svc._xrun_window_start_count == 0
        assert svc._xrun_last_known_count == 0
        assert svc._xrun_restart_cooldown_until_mono == 0.0

    def test_reset_on_resume_after_policy(self, mock_manager, mock_player, monkeypatch):
        """P2: tracking resets on resume_after_policy success."""
        _capture_xrun_events(monkeypatch)
        svc = _make_started_service(mock_manager, mock_player, device_id="dev-1")
        svc.force_stop_by_policy()
        svc._xrun_window_start_mono = 55.0
        svc._xrun_window_start_count = 3
        svc._xrun_last_known_count = 9
        svc._xrun_restart_cooldown_until_mono = time.monotonic() + 30.0

        result = svc.resume_after_policy()
        assert result["success"] is True
        assert svc._xrun_window_start_mono == 0.0
        assert svc._xrun_window_start_count == 0
        assert svc._xrun_last_known_count == 0
        assert svc._xrun_restart_cooldown_until_mono == 0.0


class TestXrunStatusFile:
    def test_write_and_read_xrun_status(self, tmp_path, monkeypatch):
        """Receiver writes status file, manager reads it back."""
        import _stream_receiver as recv_mod

        status_file = tmp_path / "receiver_xrun_status.json"
        monkeypatch.setattr(recv_mod, "_XRUN_STATUS_DIR", str(tmp_path))
        monkeypatch.setattr(recv_mod, "XRUN_STATUS_FILE", str(status_file))

        counters = {"alsa_xrun": 42, "udp_overrun": 3}
        recv_mod._write_xrun_status(counters, "test-cid")

        assert status_file.exists()

        mgr = StreamManager()
        # Patch the import inside read_xrun_status to use our file.
        with patch("_stream_receiver.XRUN_STATUS_FILE", str(status_file)):
            result = mgr.read_xrun_status()

        assert result is not None
        assert result["alsa_xrun"] == 42
        assert result["udp_overrun"] == 3
        assert result["correlation_id"] == "test-cid"


class TestXrunAutoRestartSoak:
    def test_soak_restart_budget_caps_success_events(
        self, mock_manager, mock_player, monkeypatch
    ):
        """Repeated threshold crossings must cap successful restarts by budget."""
        events = _capture_xrun_events(monkeypatch)
        svc = _make_live_service(mock_manager, mock_player)

        cycles = XRUN_MAX_RESTARTS_PER_HOUR + 2
        for _ in range(cycles):
            svc._status.active = True
            svc._status.state = "live"
            svc._active_correlation_id = "test-cid"
            svc._user_stopped = False
            svc._reset_xrun_tracking()

            mock_manager.stop_receiver.side_effect = None
            mock_manager.stop_receiver.return_value = True
            mock_manager.start_receiver.side_effect = None
            mock_manager.start_receiver.return_value = True

            # Prime window.
            mock_manager.read_xrun_status.return_value = {
                "alsa_xrun": 10,
                "udp_overrun": 0,
                "mono_ts": time.monotonic(),
                "correlation_id": "test-cid",
            }
            assert svc._check_xrun_auto_restart() is False

            # Exceed threshold.
            mock_manager.read_xrun_status.return_value = {
                "alsa_xrun": 10 + XRUN_RESTART_THRESHOLD,
                "udp_overrun": 0,
                "mono_ts": time.monotonic(),
                "correlation_id": "test-cid",
            }
            svc._check_xrun_auto_restart()

        names = [name for name, _ in events]
        assert names.count("stream_xrun_auto_restart") == XRUN_MAX_RESTARTS_PER_HOUR
        assert names.count("stream_xrun_auto_restart_skipped_throttled") >= 1
        assert len(svc._xrun_auto_restart_times) == XRUN_MAX_RESTARTS_PER_HOUR

    def test_soak_repeated_stop_race_never_emits_false_success(
        self, mock_manager, mock_player, monkeypatch
    ):
        """Stress race path: stop during restart intent must always abort."""
        events = _capture_xrun_events(monkeypatch)
        svc = _make_live_service(mock_manager, mock_player)

        loops = 40
        for _ in range(loops):
            svc._status.active = True
            svc._status.state = "live"
            svc._active_correlation_id = "test-cid"
            svc._user_stopped = False
            svc._reset_xrun_tracking()

            mock_manager.stop_receiver.side_effect = None
            mock_manager.stop_receiver.return_value = True
            mock_manager.start_receiver.side_effect = None
            mock_manager.start_receiver.return_value = True

            # Prime window.
            mock_manager.read_xrun_status.return_value = {
                "alsa_xrun": 10,
                "udp_overrun": 0,
                "mono_ts": time.monotonic(),
                "correlation_id": "test-cid",
            }
            assert svc._check_xrun_auto_restart() is False

            # Exceed threshold and force stop-race via side effect.
            mock_manager.read_xrun_status.return_value = {
                "alsa_xrun": 10 + XRUN_RESTART_THRESHOLD,
                "udp_overrun": 0,
                "mono_ts": time.monotonic(),
                "correlation_id": "test-cid",
            }

            def _stop_and_flip_state():
                svc._status.active = False
                svc._status.state = "idle"
                svc._active_correlation_id = None
                svc._user_stopped = True
                return True

            mock_manager.stop_receiver.side_effect = _stop_and_flip_state
            result = svc._check_xrun_auto_restart()
            assert result is False
            assert mock_manager.start_receiver.call_count == 0

        names = [name for name, _ in events]
        assert names.count("stream_xrun_auto_restart") == 0
        assert names.count("stream_xrun_auto_restart_aborted") == loops
