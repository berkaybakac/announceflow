"""Sender health logging: unthrottled burst at session start.

See docs/backlog.md P0 (chronic xrun onset needs finer resolution than
the steady-state 60s throttle).
"""
from __future__ import annotations
import time
from unittest.mock import MagicMock, patch

from services.stream_service import StreamService, STARTUP_HEALTH_BURST_SECONDS
from stream_manager import StreamManager


def _make_service():
    mgr = MagicMock(spec=StreamManager)
    mgr.start_receiver.return_value = True
    mgr.is_alive.return_value = True
    player = MagicMock()
    player.get_state.return_value = {"is_playing": False, "playlist": {"active": False}}
    return StreamService(stream_manager=mgr, player_fn=lambda: player)


@patch("services.stream_service.log_system")
def test_burst_window_logs_every_heartbeat(mock_log_system):
    svc = _make_service()
    svc.start(device_id="dev-1")

    for _ in range(3):
        svc.heartbeat(device_id="dev-1", sender_running=True, sender_cpu_pct=10.0)

    health_calls = [
        c for c in mock_log_system.call_args_list if c.args[0] == "stream_sender_health"
    ]
    assert len(health_calls) == 3  # no 60s throttle inside the burst window


@patch("services.stream_service.log_system")
def test_after_burst_window_throttle_resumes(mock_log_system):
    svc = _make_service()
    svc.start(device_id="dev-1")
    svc.heartbeat(device_id="dev-1", sender_running=True, sender_cpu_pct=10.0)

    with patch(
        "services.stream_service.time.monotonic",
        return_value=time.monotonic() + STARTUP_HEALTH_BURST_SECONDS + 1,
    ):
        svc.heartbeat(device_id="dev-1", sender_running=True, sender_cpu_pct=10.0)
        svc.heartbeat(device_id="dev-1", sender_running=True, sender_cpu_pct=10.0)

    health_calls = [
        c for c in mock_log_system.call_args_list if c.args[0] == "stream_sender_health"
    ]
    assert len(health_calls) == 2  # 1st (burst) + 1st post-burst; 2nd throttled
