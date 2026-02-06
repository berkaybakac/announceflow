"""
AnnounceFlow - Player Routes
API endpoints for player control.
"""
import logging
from flask import Blueprint, jsonify, request
import database as db
from services.config_service import load_config
from scheduler import is_within_working_hours
from player import get_player
from scheduler import get_scheduler
from logger import log_web
from utils.helpers import (
    login_required,
    _json_success,
    _json_error,
    _get_media_or_404,
    _reject_if_outside_working_hours,
)


player_bp = Blueprint("player", __name__)
logger = logging.getLogger(__name__)


@player_bp.route("/api/health")
def api_health():
    """System health check endpoint (no auth required)."""
    import time as time_module

    player = get_player()
    scheduler = get_scheduler()

    return jsonify(
        {
            "status": "ok",
            "player": {
                "is_playing": player.is_playing,
                "backend": player.get_state().get("backend"),
                "volume": player.get_volume(),
            },
            "scheduler": {"running": scheduler._running},
            "timestamp": int(time_module.time()),
        }
    )


@player_bp.route("/api/play", methods=["POST"])
@login_required
def api_play():
    """Play a media file."""
    blocked = _reject_if_outside_working_hours()
    if blocked:
        return blocked

    data = request.get_json() or {}
    media_id = data.get("media_id")

    if not media_id:
        return _json_error("media_id required", 400)

    media, error = _get_media_or_404(media_id)
    if error:
        return error

    player = get_player()
    playlist_was_active = player._playlist_active and len(player._playlist) > 0
    logger.info(
        f"[source] manual play -> {media['filename']} (media_id={media_id})"
    )
    success = player.play(media["filepath"], preserve_playlist=playlist_was_active)

    if success:
        db.update_playback_state(
            current_media_id=media_id, is_playing=True, position_seconds=0
        )
        log_web("play", {"media_id": media_id, "filename": media["filename"]})

    return _json_success({"success": success})


@player_bp.route("/api/stop", methods=["POST"])
@login_required
def api_stop():
    """Stop playback."""
    player = get_player()
    success = player.stop()
    db.update_playback_state(current_media_id=0, is_playing=False, position_seconds=0)
    log_web("stop", {})
    return _json_success({"success": success})


@player_bp.route("/api/stop-preview", methods=["POST"])
@login_required
def api_stop_preview():
    """Stop preview playback without breaking playlist loop."""
    player = get_player()
    config = load_config()
    resume_allowed = is_within_working_hours(config)

    playlist_was_active = player._playlist_active and len(player._playlist) > 0
    if not resume_allowed and playlist_was_active:
        scheduler = get_scheduler()
        if scheduler._working_hours_pause_state is None:
            scheduler._working_hours_pause_state = {
                "playlist": list(player._playlist),
                "index": player._playlist_index,
                "loop": player._playlist_loop,
                "active": True,
            }
        player._playlist_active = False
        db.save_playlist_state(
            playlist=list(player._playlist),
            index=player._playlist_index,
            loop=player._playlist_loop,
            active=True,
        )

    success = player.stop_preview(resume_allowed=resume_allowed)
    log_web("stop_preview", {"resume_allowed": resume_allowed})
    return _json_success({"success": success})


@player_bp.route("/api/volume", methods=["POST"])
@login_required
def api_volume():
    """Set volume level."""
    data = request.get_json(silent=True) or {}
    if not isinstance(data, dict):
        return _json_error("Geçersiz istek gövdesi", 400)

    raw_volume = data.get("volume", 80)
    try:
        volume = int(raw_volume)
    except (TypeError, ValueError):
        return _json_error("Volume 0-100 arasında sayı olmalı", 400)

    if not 0 <= volume <= 100:
        return _json_error("Volume 0-100 arasında olmalı", 400)

    player = get_player()
    success = player.set_volume(volume)
    db.update_playback_state(volume=volume)
    log_web("volume", {"volume": volume})

    return _json_success({"success": success, "volume": volume})


@player_bp.route("/api/now-playing")
@login_required
def api_now_playing():
    """Get current player state."""
    player = get_player()
    state = player.get_state()
    db_state = db.get_playback_state()
    state["volume"] = db_state.get("volume", 80)

    # Get duration from database if file is playing
    if state.get("filename"):
        media = db.get_media_by_filename(state["filename"])
        if media:
            state["duration_seconds"] = media.get("duration_seconds", 0)

    return jsonify(state)


@player_bp.route("/api/media/music")
@login_required
def api_get_music_files():
    """Get all music files for playlist display."""
    files = db.get_all_media_files(media_type="music")
    return jsonify({"files": files, "count": len(files)})


@player_bp.route("/api/pause", methods=["POST"])
@login_required
def api_pause():
    """Deprecated."""
    return _json_error("Not supported", 405)


@player_bp.route("/api/resume", methods=["POST"])
@login_required
def api_resume():
    """Deprecated."""
    return _json_error("Not supported", 405)
