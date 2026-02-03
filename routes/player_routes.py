"""
AnnounceFlow - Player Routes
API endpoints for player control.
"""
from flask import Blueprint, jsonify, request
import database as db
from player import get_player
from scheduler import get_scheduler
from logger import log_web
from utils.helpers import login_required, _json_success, _json_error, _get_media_or_404


player_bp = Blueprint('player', __name__)


@player_bp.route('/api/health')
def api_health():
    """System health check endpoint (no auth required)."""
    import time as time_module
    player = get_player()
    scheduler = get_scheduler()

    return jsonify({
        'status': 'ok',
        'player': {
            'is_playing': player.is_playing,
            'backend': player.get_state().get('backend'),
            'volume': player.get_volume()
        },
        'scheduler': {
            'running': scheduler._running
        },
        'timestamp': int(time_module.time())
    })


@player_bp.route('/api/play', methods=['POST'])
@login_required
def api_play():
    """Play a media file."""
    data = request.get_json() or {}
    media_id = data.get('media_id')

    if not media_id:
        return _json_error('media_id required', 400)

    media, error = _get_media_or_404(media_id)
    if error:
        return error

    player = get_player()
    success = player.play(media['filepath'])

    if success:
        db.update_playback_state(current_media_id=media_id, is_playing=True, position_seconds=0)
        log_web("play", {"media_id": media_id, "filename": media['filename']})

    return _json_success({'success': success})


@player_bp.route('/api/stop', methods=['POST'])
@login_required
def api_stop():
    """Stop playback."""
    player = get_player()
    success = player.stop()
    db.update_playback_state(current_media_id=0, is_playing=False, position_seconds=0)
    log_web("stop", {})
    return _json_success({'success': success})


@player_bp.route('/api/volume', methods=['POST'])
@login_required
def api_volume():
    """Set volume level."""
    data = request.get_json() or {}
    volume = data.get('volume', 80)

    player = get_player()
    success = player.set_volume(volume)
    db.update_playback_state(volume=volume)
    log_web("volume", {"volume": volume})

    return _json_success({'success': success, 'volume': volume})


@player_bp.route('/api/now-playing')
@login_required
def api_now_playing():
    """Get current player state."""
    player = get_player()
    state = player.get_state()
    db_state = db.get_playback_state()
    state['volume'] = db_state.get('volume', 80)

    # Get duration from database if file is playing
    if state.get('filename'):
        media = db.get_media_by_filename(state['filename'])
        if media:
            state['duration_seconds'] = media.get('duration_seconds', 0)

    return jsonify(state)


@player_bp.route('/api/media/music')
@login_required
def api_get_music_files():
    """Get all music files for playlist display."""
    files = db.get_all_media_files(media_type='music')
    return jsonify({
        'files': files,
        'count': len(files)
    })
