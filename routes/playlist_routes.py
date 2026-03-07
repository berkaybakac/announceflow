"""
AnnounceFlow - Playlist Routes
API endpoints for playlist management.
"""
import logging
from flask import Blueprint, request
import database as db
from player import get_player
from services.stream_service import get_stream_service
from utils.helpers import (
    login_required,
    _json_success,
    _json_error,
    _reject_if_outside_working_hours,
)


playlist_bp = Blueprint("playlist", __name__)
logger = logging.getLogger(__name__)


def _reject_if_stream_active():
    """Return error response if a stream is currently active, else None."""
    status = get_stream_service().status()
    if status.get("active") and status.get("state") == "live":
        return _json_error("Stream aktif — önce yayını durdurun", 409)
    return None


@playlist_bp.route("/api/playlist/set", methods=["POST"])
@login_required
def api_playlist_set():
    """Set a playlist of media files."""
    blocked = _reject_if_stream_active()
    if blocked:
        return blocked

    data = request.get_json() or {}
    media_ids = data.get("media_ids", [])
    loop = data.get("loop", True)

    if not media_ids:
        return _json_error("media_ids required", 400)

    # Get file paths for all media IDs
    file_paths = []
    for media_id in media_ids:
        media = db.get_media_file(media_id)
        if media:
            file_paths.append(media["filepath"])

    if not file_paths:
        return _json_error("No valid media files", 404)

    player = get_player()
    success = player.set_playlist(file_paths, loop=loop)

    return _json_success({"success": success, "tracks": len(file_paths)})


@playlist_bp.route("/api/playlist/play", methods=["POST"])
@login_required
def api_playlist_play():
    """Start playing the playlist."""
    blocked = _reject_if_outside_working_hours()
    if blocked:
        return blocked
    blocked = _reject_if_stream_active()
    if blocked:
        return blocked

    player = get_player()
    logger.info("[source] manual play -> playlist current track")
    success = player.play_playlist()

    if success:
        db.update_playback_state(is_playing=True)

    return _json_success({"success": success})


@playlist_bp.route("/api/playlist/next", methods=["POST"])
@login_required
def api_playlist_next():
    """Skip to next track in playlist."""
    blocked = _reject_if_outside_working_hours()
    if blocked:
        return blocked
    blocked = _reject_if_stream_active()
    if blocked:
        return blocked

    player = get_player()
    # Guard: "next" should only work while loop/playlist mode is active.
    # If user previously pressed Stop, keep system stopped.
    if not player._playlist_active or len(player._playlist) == 0:
        logger.info("[source] manual next ignored -> playlist inactive")
        return _json_success({"success": True, "ignored": True, "reason": "playlist_inactive"})

    logger.info("[source] manual play -> playlist next track")
    success = player.play_next()
    return _json_success({"success": success})


@playlist_bp.route("/api/playlist/stop", methods=["POST"])
@login_required
def api_playlist_stop():
    """Stop playlist and clear it."""
    player = get_player()
    player.stop_playlist()
    db.update_playback_state(current_media_id=0, is_playing=False)
    return _json_success()


@playlist_bp.route("/api/playlist/start-all", methods=["POST"])
@login_required
def api_playlist_start_all():
    """Start playlist with ALL music files in library (loop mode)."""
    blocked = _reject_if_outside_working_hours()
    if blocked:
        return blocked
    blocked = _reject_if_stream_active()
    if blocked:
        return blocked

    # Get all music files from library
    music_files = db.get_all_media_files("music")

    if not music_files:
        return _json_error("Kütüphanede müzik yok", 404)

    # Get file paths
    file_paths = [f["filepath"] for f in music_files]

    # Set playlist and start playing (loop=True)
    player = get_player()
    player.set_playlist(file_paths, loop=True)
    logger.info(f"[source] manual play -> playlist start-all (tracks={len(file_paths)})")
    success = player.play_playlist()

    if success:
        db.update_playback_state(is_playing=True)

    return _json_success({"success": success, "tracks": len(file_paths)})
