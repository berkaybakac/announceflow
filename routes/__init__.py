"""
AnnounceFlow Routes
Blueprint registration for modular route organization.
"""
from .player_routes import player_bp


def register_blueprints(app):
    """Register all blueprints with the Flask app.

    Args:
        app: Flask application instance
    """
    # Phase 3.1a: Player core endpoints (health, play, stop, volume)
    app.register_blueprint(player_bp)

    # Phase 3.1b: Player info endpoints (now-playing, media/music)
    # Phase 3.1c: Player deprecated endpoints (pause, resume)
    # Phase 3.2: playlist_bp (playlist API endpoints)
    # Phase 3.3: media_bp (media API endpoints)
    # Phase 3.4: schedule_bp (schedule API endpoints)
    # Phase 3.5: settings_bp (settings API endpoints)
    # Phase 3.6: auth_bp (auth routes)
