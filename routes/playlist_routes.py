"""
AnnounceFlow - Playlist Routes
API endpoints for playlist management.
"""
from flask import Blueprint, request
import database as db
from player import get_player
from utils.helpers import login_required, _json_success, _json_error


playlist_bp = Blueprint('playlist', __name__)


@playlist_bp.route('/api/playlist/set', methods=['POST'])
@login_required
def api_playlist_set():
    """Set a playlist of media files."""
    data = request.get_json() or {}
    media_ids = data.get('media_ids', [])
    loop = data.get('loop', True)

    if not media_ids:
        return _json_error('media_ids required', 400)

    # Get file paths for all media IDs
    file_paths = []
    for media_id in media_ids:
        media = db.get_media_file(media_id)
        if media:
            file_paths.append(media['filepath'])

    if not file_paths:
        return _json_error('No valid media files', 404)

    player = get_player()
    success = player.set_playlist(file_paths, loop=loop)

    return _json_success({'success': success, 'tracks': len(file_paths)})


@playlist_bp.route('/api/playlist/play', methods=['POST'])
@login_required
def api_playlist_play():
    """Start playing the playlist."""
    player = get_player()
    success = player.play_playlist()

    if success:
        db.update_playback_state(is_playing=True)

    return _json_success({'success': success})


@playlist_bp.route('/api/playlist/next', methods=['POST'])
@login_required
def api_playlist_next():
    """Skip to next track in playlist."""
    player = get_player()
    success = player.play_next()
    return _json_success({'success': success})


@playlist_bp.route('/api/playlist/stop', methods=['POST'])
@login_required
def api_playlist_stop():
    """Stop playlist and clear it."""
    player = get_player()
    player.stop_playlist()
    db.update_playback_state(current_media_id=0, is_playing=False)
    return _json_success()


@playlist_bp.route('/api/playlist/start-all', methods=['POST'])
@login_required
def api_playlist_start_all():
    """Start playlist with ALL music files in library (loop mode)."""
    # Get all music files from library
    music_files = db.get_all_media_files('music')

    if not music_files:
        return _json_error('Kütüphanede müzik yok', 404)

    # Get file paths
    file_paths = [f['filepath'] for f in music_files]

    # Set playlist and start playing (loop=True)
    player = get_player()
    player.set_playlist(file_paths, loop=True)
    success = player.play_playlist()

    if success:
        db.update_playback_state(is_playing=True)

    return _json_success({'success': success, 'tracks': len(file_paths)})
