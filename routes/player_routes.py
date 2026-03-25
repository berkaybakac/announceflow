"""
AnnounceFlow - Player Routes
API endpoints for player control.
"""
import logging
import os
import threading
import socket
import uuid
from typing import Optional
from flask import Blueprint, jsonify, request
import database as db
from services.config_service import load_config, save_config
from services.silence_policy import resolve_silence_policy
from services.volume_runtime_service import get_volume_runtime_service
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
_preview_lock = threading.Lock()
_preview_context = {"media_id": None, "playback_session": None}
_DEFAULT_VOLUME = 80
_volume_runtime = get_volume_runtime_service()


def _set_preview_context(media_id: int, playback_session: Optional[int]) -> None:
    """Track currently active library preview session."""
    with _preview_lock:
        _preview_context["media_id"] = media_id
        _preview_context["playback_session"] = playback_session


def _clear_preview_context() -> None:
    """Clear tracked library preview session."""
    with _preview_lock:
        _preview_context["media_id"] = None
        _preview_context["playback_session"] = None


def _get_preview_context() -> dict:
    """Get a snapshot of tracked preview session."""
    with _preview_lock:
        return dict(_preview_context)


def _resolve_instance_identity() -> tuple[str, str]:
    """Return stable server identity for agent discovery checks."""
    config = load_config()
    instance_id = str(config.get("instance_id", "")).strip()
    site_name = str(config.get("site_name", "")).strip()
    dirty = False

    if not instance_id:
        instance_id = f"af-{uuid.uuid4().hex[:12]}"
        config["instance_id"] = instance_id
        dirty = True

    if not site_name:
        site_name = str(config.get("device_name", "")).strip() or socket.gethostname()

    if dirty:
        save_config(config)

    return instance_id, site_name


def _canonical_volume_state(raw: Optional[dict]) -> dict:
    """Normalize canonical volume state payload for API responses."""
    raw = raw or {}
    try:
        volume = int(raw.get("volume", _DEFAULT_VOLUME))
    except (TypeError, ValueError, AttributeError):
        volume = _DEFAULT_VOLUME
    volume = max(0, min(100, volume))

    try:
        last_nonzero = int(raw.get("last_nonzero_volume", volume if volume > 0 else _DEFAULT_VOLUME))
    except (TypeError, ValueError, AttributeError):
        last_nonzero = volume if volume > 0 else _DEFAULT_VOLUME
    last_nonzero = max(0, min(100, last_nonzero))
    if last_nonzero <= 0:
        last_nonzero = _DEFAULT_VOLUME

    try:
        revision = int(raw.get("volume_revision", 0))
    except (TypeError, ValueError, AttributeError):
        revision = 0
    revision = max(0, revision)

    muted_raw = raw.get("muted")
    muted = bool(muted_raw) if isinstance(muted_raw, bool) else (volume <= 0)

    return {
        "volume": volume,
        "muted": muted,
        "last_nonzero_volume": last_nonzero,
        "volume_revision": revision,
    }


@player_bp.route("/api/health")
def api_health():
    """System health check endpoint (no auth required)."""
    import time as time_module

    player = get_player()
    scheduler = get_scheduler()
    instance_id, site_name = _resolve_instance_identity()

    return jsonify(
        {
            "status": "ok",
            "player": {
                "is_playing": player.is_playing,
                "backend": player.get_state().get("backend"),
                "volume": player.get_volume(),
            },
            "scheduler": {"running": scheduler.is_running()},
            "identity": {
                "instance_id": instance_id,
                "site_name": site_name,
            },
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
    is_library_preview = bool(data.get("library_preview", False))

    if not media_id:
        return _json_error("media_id required", 400)

    media, error = _get_media_or_404(media_id)
    if error:
        return error

    player = get_player()
    is_announcement = str(media.get("media_type", "")).strip().lower() == "announcement"
    silence_decision = None
    if is_announcement:
        config = load_config()
        silence_decision = resolve_silence_policy(
            config,
            allow_network=False,
            fail_safe_on_unknown=True,
        )
        if silence_decision.get("silence_active", False):
            return _json_error(
                "Sessizlik politikası aktifken anons oynatılamaz",
                403,
            )

    playlist_was_active = player._playlist_active and len(player._playlist) > 0
    canonical_volume = _canonical_volume_state(db.get_volume_state())
    override_applied = False
    override_volume = 0
    if is_announcement and canonical_volume["muted"]:
        override_volume = max(1, canonical_volume["last_nonzero_volume"])
        override_applied = bool(player.set_volume(override_volume))
        if not override_applied:
            logger.warning("Manual announcement mute override could not set player volume")

    logger.info(
        f"[source] manual play -> {media['filename']} (media_id={media_id})"
    )
    success = player.play(media["filepath"], preserve_playlist=playlist_was_active)

    if success:
        if override_applied:
            _volume_runtime.activate_announcement_override(
                playback_session=getattr(player, "_playback_session", None),
                effective_volume=override_volume,
                source="manual_announcement",
            )
        db.update_playback_state(
            current_media_id=media_id, is_playing=True, position_seconds=0
        )
        log_web("play", {"media_id": media_id, "filename": media["filename"]})
        playback_session = getattr(player, "_playback_session", None)
        if is_library_preview:
            _set_preview_context(int(media_id), playback_session)
        else:
            _clear_preview_context()
    else:
        if override_applied:
            player.set_volume(canonical_volume["volume"])
        if is_library_preview:
            _clear_preview_context()

    return _json_success({"success": success})


@player_bp.route("/api/stop", methods=["POST"])
@login_required
def api_stop():
    """Stop playback."""
    player = get_player()
    success = player.stop()
    _volume_runtime.restore_override(reason="manual_stop")
    _clear_preview_context()
    db.update_playback_state(current_media_id=0, is_playing=False, position_seconds=0)
    log_web("stop", {})
    return _json_success({"success": success})


@player_bp.route("/api/stop-preview", methods=["POST"])
@login_required
def api_stop_preview():
    """Stop preview playback without breaking playlist loop."""
    data = request.get_json(silent=True) or {}
    raw_media_id = data.get("media_id")
    try:
        media_id = int(raw_media_id)
        if media_id <= 0:
            raise ValueError("must be positive")
    except (TypeError, ValueError):
        return _json_error("media_id required", 400)

    player = get_player()
    media, error = _get_media_or_404(media_id)
    if error:
        return _json_success({"success": True, "ignored": True, "reason": "media_not_found"})

    preview_ctx = _get_preview_context()
    current_session = getattr(player, "_playback_session", None)
    current_path = os.path.abspath(player.current_file) if player.current_file else ""
    expected_path = os.path.abspath(media["filepath"])
    is_active_preview_target = (
        preview_ctx.get("media_id") == media_id
        and preview_ctx.get("playback_session") == current_session
        and player.is_playing
        and current_path == expected_path
    )

    # Ignore stop requests unless the row belongs to active preview playback.
    if not is_active_preview_target:
        log_web(
            "stop_preview_ignored",
            {
                "requested_media_id": media_id,
                "preview_media_id": preview_ctx.get("media_id"),
            },
        )
        return _json_success({"success": True, "ignored": True})

    config = load_config()
    resume_allowed = is_within_working_hours(config)

    playlist_was_active = player._playlist_active and len(player._playlist) > 0
    if not resume_allowed and playlist_was_active:
        scheduler = get_scheduler()
        if not scheduler.has_deferred_restore("working_hours"):
            scheduler.defer_playlist_restore(
                "working_hours",
                {
                "playlist": list(player._playlist),
                "index": player._playlist_index,
                "loop": player._playlist_loop,
                "active": True,
                },
            )
        player.apply_playlist_state(runtime_active=False, db_active=True)

    success = player.stop_preview(resume_allowed=resume_allowed)
    _clear_preview_context()
    log_web("stop_preview", {"resume_allowed": resume_allowed})
    return _json_success({"success": success})


@player_bp.route("/api/volume", methods=["POST"])
@login_required
def api_volume():
    """Set canonical volume state (absolute volume or mute intent)."""
    data = request.get_json(silent=True) or {}
    if not isinstance(data, dict):
        return _json_error("Geçersiz istek gövdesi", 400)

    has_volume = "volume" in data
    has_muted = "muted" in data
    if has_volume == has_muted:
        return _json_error("İstek yalnızca volume veya muted içermeli", 400)

    volume: Optional[int] = None
    muted: Optional[bool] = None
    if has_volume:
        raw_volume = data.get("volume")
        try:
            volume = int(raw_volume)
        except (TypeError, ValueError):
            return _json_error("Volume 0-100 arasında sayı olmalı", 400)
        if not 0 <= volume <= 100:
            return _json_error("Volume 0-100 arasında olmalı", 400)
    else:
        raw_muted = data.get("muted")
        if not isinstance(raw_muted, bool):
            return _json_error("muted true/false olmalı", 400)
        muted = raw_muted

    current_state = _canonical_volume_state(db.get_volume_state())
    target_volume = volume if volume is not None else (0 if muted else current_state["last_nonzero_volume"])
    _volume_runtime.cancel_override(reason="user_volume_intent", restore=False)

    player = get_player()
    prev_volume = player.get_volume()
    success = player.set_volume(target_volume)
    if success:
        canonical_state = _canonical_volume_state(db.set_volume_state(target_volume))
        canonical_state.update(
            _volume_runtime.get_effective_state(
                canonical_state,
                player.get_volume(),
            )
        )
        if prev_volume != target_volume:
            log_web("volume", {"volume": target_volume})
        return _json_success({"success": True, **canonical_state})

    # Keep DB as-is when player volume write fails.
    current_state.update(
        _volume_runtime.get_effective_state(
            current_state,
            player.get_volume(),
        )
    )
    return _json_success({"success": False, **current_state})


@player_bp.route("/api/now-playing")
@login_required
def api_now_playing():
    """Get current player state."""
    player = get_player()
    state = player.get_state()
    runtime_player_volume = state.get("volume")
    canonical_volume = _canonical_volume_state(db.get_volume_state())
    state["volume"] = canonical_volume["volume"]
    state["muted"] = canonical_volume["muted"]
    state["last_nonzero_volume"] = canonical_volume["last_nonzero_volume"]
    state["volume_revision"] = canonical_volume["volume_revision"]
    state.update(
        _volume_runtime.get_effective_state(canonical_volume, runtime_player_volume)
    )

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
