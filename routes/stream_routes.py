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
from flask import Blueprint, jsonify
from utils.helpers import login_required

stream_bp = Blueprint("stream", __name__)


@stream_bp.route("/api/stream/start", methods=["POST"])
@login_required
def stream_start():
    """Start a stream session."""
    # TODO(Faz 3): Wire to StreamService.start()
    return jsonify({"success": False, "error": "not_implemented"}), 501


@stream_bp.route("/api/stream/stop", methods=["POST"])
@login_required
def stream_stop():
    """Stop the active stream session."""
    # TODO(Faz 3): Wire to StreamService.stop()
    return jsonify({"success": False, "error": "not_implemented"}), 501


@stream_bp.route("/api/stream/status", methods=["GET"])
@login_required
def stream_status():
    """Get current stream status."""
    # TODO(Faz 3): Wire to StreamService.status()
    return jsonify({
        "active": False,
        "state": "idle",
        "source_before_stream": "none",
        "last_error": None,
    })
