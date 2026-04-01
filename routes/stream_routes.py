"""
AnnounceFlow - Stream Routes
HTTP endpoints for stream control: start, stop, status, heartbeat.

Responsibilities:
- HTTP request/response handling and input validation
- Delegates all business logic to StreamService
- No business rules in this layer

V1 API contract (PI4_STREAM_V1_SCOPE.md section 4):
    POST /api/stream/start
    POST /api/stream/stop
    GET  /api/stream/status
    POST /api/stream/heartbeat
"""
import logging
import math
from typing import Optional

from flask import Blueprint, jsonify, request

from logger import log_system
from services.audio_alert_service import clamp_window_minutes, get_audio_alerts
from services.stream_service import get_stream_service
from utils.helpers import _json_error, _json_success, login_required

stream_bp = Blueprint("stream", __name__)
logger = logging.getLogger(__name__)

_stream_service = get_stream_service()


@stream_bp.route("/api/stream/start", methods=["POST"])
@login_required
def stream_start():
    """Start stream.

    Agent call (device header present): immediate receiver start.
    Panel call (no device header): desired-state command queued for agent.
    """
    correlation_id = request.headers.get("X-Stream-Correlation-Id", "").strip() or None
    device_id = request.headers.get("X-Stream-Device-Id", "").strip() or None
    device_name = request.headers.get("X-Stream-Device-Name", "").strip() or None
    if correlation_id or device_id:
        log_system(
            "stream_start_api_request",
            {
                "source": "agent_direct_start",
                "correlation_id": correlation_id,
                "device_id": device_id,
                "device_name": device_name,
                "remote_addr": request.remote_addr,
                "user_agent": request.headers.get("User-Agent", ""),
            },
        )
        result = _stream_service.start(
            correlation_id=correlation_id,
            device_id=device_id,
            device_name=device_name,
        )
    else:
        payload = request.get_json(silent=True) or {}
        target_device_id = None
        if isinstance(payload, dict):
            target_device_id = str(payload.get("target_device_id") or "").strip() or None
        result = _stream_service.request_remote_state(
            should_stream=True,
            issued_by="panel",
            target_device_id=target_device_id,
        )
    if result["success"]:
        control = result.get("control")
        if not isinstance(control, dict):
            control = None
        return _json_success(status=result["status"], control=control)
    if result.get("error") in {
        "no_agent_available",
        "preferred_device_not_set",
        "preferred_device_offline",
    }:
        return _json_error(result.get("error"), status=409)
    if result.get("error") == "stream_already_live":
        return _json_error("stream_already_live", status=409)
    if result.get("error") == "takeover_in_progress":
        return _json_error("takeover_in_progress", status=409)
    return _json_error(
        result.get("error") or result["status"].get("last_error", "stream_start_failed"),
        status=500,
    )


@stream_bp.route("/api/stream/stop", methods=["POST"])
@login_required
def stream_stop():
    """Stop stream.

    Agent call (device header present): immediate receiver stop.
    Panel call (no device header): desired-state command queued for agent.
    """
    device_id = request.headers.get("X-Stream-Device-Id", "").strip() or None
    if device_id:
        stop_origin = request.headers.get("X-Stream-Stop-Origin", "").strip() or None
        log_system(
            "stream_stop_api_request",
            {
                "source": "agent_direct_stop",
                "device_id": device_id,
                "device_name": request.headers.get("X-Stream-Device-Name", "").strip() or None,
                "stop_origin": stop_origin,
                "remote_addr": request.remote_addr,
                "user_agent": request.headers.get("User-Agent", ""),
            },
        )
        result = _stream_service.stop(
            caller="routes.stream_stop",
            reason=stop_origin or "api_stop_request",
        )
    else:
        payload = request.get_json(silent=True) or {}
        target_device_id = None
        if isinstance(payload, dict):
            target_device_id = str(payload.get("target_device_id") or "").strip() or None
        result = _stream_service.request_remote_state(
            should_stream=False,
            issued_by="panel",
            target_device_id=target_device_id,
        )
    if result["success"]:
        control = result.get("control")
        if not isinstance(control, dict):
            control = None
        return _json_success(status=result["status"], control=control)
    if result.get("error") in {
        "no_agent_available",
        "preferred_device_not_set",
        "preferred_device_offline",
    }:
        return _json_error(result.get("error"), status=409)
    return _json_error(
        result["status"].get("last_error", "stream_stop_failed"),
        status=500,
    )


@stream_bp.route("/api/stream/status", methods=["GET"])
@login_required
def stream_status():
    """Get current stream status."""
    return jsonify(_stream_service.status())


@stream_bp.route("/api/stream/alerts", methods=["GET"])
@login_required
def stream_alerts():
    """Get thresholded audio alerts from recent stream events."""
    window_minutes = clamp_window_minutes(request.args.get("window_minutes", ""))
    alerts = get_audio_alerts(window_minutes=window_minutes)
    return _json_success(alerts=alerts)


@stream_bp.route("/api/stream/heartbeat", methods=["POST"])
@login_required
def stream_heartbeat():
    """Process keepalive + control poll over a single agent heartbeat."""
    device_id = request.headers.get("X-Stream-Device-Id", "").strip() or None
    device_name = request.headers.get("X-Stream-Device-Name", "").strip() or None
    last_applied_raw = (
        request.headers.get("X-Stream-Last-Applied-Generation", "").strip() or None
    )
    last_applied_generation = None
    if last_applied_raw is not None:
        try:
            last_applied_generation = int(last_applied_raw)
        except ValueError:
            last_applied_generation = None
    last_command_id = request.headers.get("X-Stream-Last-Command-Id", "").strip() or None
    last_command_result = (
        request.headers.get("X-Stream-Last-Command-Result", "").strip() or None
    )
    last_command_error = (
        request.headers.get("X-Stream-Last-Command-Error", "").strip() or None
    )
    sender_running_raw = (
        request.headers.get("X-Stream-Sender-Running", "").strip().lower() or None
    )
    sender_running = None
    if sender_running_raw in {"1", "true", "yes", "on"}:
        sender_running = True
    elif sender_running_raw in {"0", "false", "no", "off"}:
        sender_running = False

    def _parse_float_header(
        name: str,
        *,
        min_value: Optional[float] = None,
        max_value: Optional[float] = None,
    ) -> Optional[float]:
        raw = request.headers.get(name, "").strip()
        if not raw:
            return None
        try:
            parsed = float(raw)
        except ValueError:
            return None
        if not math.isfinite(parsed):
            return None
        if min_value is not None and parsed < min_value:
            return None
        if max_value is not None and parsed > max_value:
            return None
        return round(parsed, 3)

    def _parse_int_header(
        name: str,
        *,
        min_value: Optional[int] = None,
        max_value: Optional[int] = None,
    ) -> Optional[int]:
        raw = request.headers.get(name, "").strip()
        if not raw:
            return None
        try:
            parsed = int(raw)
        except ValueError:
            return None
        if min_value is not None and parsed < min_value:
            return None
        if max_value is not None and parsed > max_value:
            return None
        return parsed

    sender_cpu_pct = _parse_float_header(
        "X-Stream-Sender-CPU-Pct",
        min_value=0.0,
        max_value=100.0,
    )
    sender_mem_used_pct = _parse_float_header(
        "X-Stream-Sender-Mem-Used-Pct",
        min_value=0.0,
        max_value=100.0,
    )
    sender_mem_available_mb = _parse_int_header(
        "X-Stream-Sender-Mem-Available-Mb",
        min_value=0,
    )
    sender_wifi_signal_pct = _parse_int_header(
        "X-Stream-Sender-Wifi-Signal-Pct",
        min_value=0,
        max_value=100,
    )
    sender_wifi_ssid = (
        request.headers.get("X-Stream-Sender-Wifi-Ssid", "").strip() or None
    )
    if sender_wifi_ssid and len(sender_wifi_ssid) > 128:
        sender_wifi_ssid = sender_wifi_ssid[:128]

    result = _stream_service.heartbeat(
        device_id=device_id,
        device_name=device_name,
        last_applied_generation=last_applied_generation,
        last_command_id=last_command_id,
        last_command_result=last_command_result,
        last_command_error=last_command_error,
        sender_running=sender_running,
        sender_cpu_pct=sender_cpu_pct,
        sender_mem_used_pct=sender_mem_used_pct,
        sender_mem_available_mb=sender_mem_available_mb,
        sender_wifi_signal_pct=sender_wifi_signal_pct,
        sender_wifi_ssid=sender_wifi_ssid,
    )
    return _json_success(
        status=result.get("status"),
        accepted=result.get("accepted"),
        reason=result.get("reason"),
        owner_device_id=result.get("owner_device_id"),
        control=result.get("control"),
    )
