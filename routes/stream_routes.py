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

from flask import Blueprint, jsonify, request

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
        if not isinstance(result, dict):
            # Test doubles that don't implement request_remote_state yet.
            result = _stream_service.start(device_name=device_name)
    if result["success"]:
        control = result.get("control")
        if not isinstance(control, dict):
            control = None
        return _json_success(status=result["status"], control=control)
    if result.get("error") == "no_agent_available":
        return _json_error("no_agent_available", status=409)
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
        result = _stream_service.stop()
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
        if not isinstance(result, dict):
            # Test doubles that don't implement request_remote_state yet.
            result = _stream_service.stop()
    if result["success"]:
        control = result.get("control")
        if not isinstance(control, dict):
            control = None
        return _json_success(status=result["status"], control=control)
    if result.get("error") == "no_agent_available":
        return _json_error("no_agent_available", status=409)
    return _json_error(
        result["status"].get("last_error", "stream_stop_failed"),
        status=500,
    )


@stream_bp.route("/api/stream/status", methods=["GET"])
@login_required
def stream_status():
    """Get current stream status."""
    return jsonify(_stream_service.status())


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

    result = _stream_service.heartbeat(
        device_id=device_id,
        device_name=device_name,
        last_applied_generation=last_applied_generation,
        last_command_id=last_command_id,
        last_command_result=last_command_result,
        last_command_error=last_command_error,
        sender_running=sender_running,
    )
    return _json_success(
        status=result.get("status"),
        accepted=result.get("accepted"),
        reason=result.get("reason"),
        owner_device_id=result.get("owner_device_id"),
        control=result.get("control"),
    )
