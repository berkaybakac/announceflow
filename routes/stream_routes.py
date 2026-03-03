"""
AnnounceFlow - Stream Routes
HTTP endpoints for stream control: start, stop, status.

Responsibilities:
- HTTP request/response handling and input validation
- Delegates all business logic to StreamService
- No business rules in this layer

V1 API contract (PI4_STREAM_V1_SCOPE.md section 4):
    POST /api/stream/start
    POST /api/stream/stop
    GET  /api/stream/status
"""
import logging

from flask import Blueprint, jsonify

from services.stream_service import get_stream_service
from utils.helpers import _json_error, _json_success, login_required

stream_bp = Blueprint("stream", __name__)
logger = logging.getLogger(__name__)

_stream_service = get_stream_service()


@stream_bp.route("/api/stream/start", methods=["POST"])
@login_required
def stream_start():
    """Start a stream session."""
    result = _stream_service.start()
    if result["success"]:
        return _json_success(status=result["status"])
    return _json_error(
        result["status"].get("last_error", "stream_start_failed"),
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
