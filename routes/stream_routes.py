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
    """Start a stream session."""
    correlation_id = request.headers.get("X-Stream-Correlation-Id", "").strip() or None
    device_id = request.headers.get("X-Stream-Device-Id", "").strip() or None
    if correlation_id or device_id:
        result = _stream_service.start(
            correlation_id=correlation_id,
            device_id=device_id,
        )
    else:
        result = _stream_service.start()
    if result["success"]:
        return _json_success(status=result["status"])
    if result.get("error") == "stream_already_live":
        return _json_error("stream_already_live", status=409)
    return _json_error(
        result.get("error") or result["status"].get("last_error", "stream_start_failed"),
        status=500,
    )


@stream_bp.route("/api/stream/stop", methods=["POST"])
@login_required
def stream_stop():
    """Stop the active stream session."""
    result = _stream_service.stop()
    if result["success"]:
        return _json_success(status=result["status"])
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
    """Record a sender heartbeat to keep the stream alive.

    Senders call this every ~5 s while streaming.  If no heartbeat is
    received for HEARTBEAT_TIMEOUT (15 s), the stream is auto-stopped.

    Header:
        X-Stream-Device-Id: <device_id>  (same value used in /start)

    Responses:
        200  heartbeat accepted
        409  caller is not the current stream owner
        400  no stream is currently active
    """
    device_id = request.headers.get("X-Stream-Device-Id", "").strip() or None
    result = _stream_service.heartbeat(device_id=device_id)
    if result["accepted"]:
        return _json_success(status=result["status"])
    reason = result.get("reason", "heartbeat_rejected")
    if reason == "not_owner":
        return _json_error("not_stream_owner", status=409)
    return _json_error(reason, status=400)
