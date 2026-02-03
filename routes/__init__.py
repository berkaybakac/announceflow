"""
AnnounceFlow Routes
Blueprint registration for modular route organization.
"""
from .player_routes import player_bp
from .playlist_routes import playlist_bp
from .media_routes import media_bp
from .schedule_routes import schedule_bp


def register_blueprints(app):
    """Register all blueprints with the Flask app.

    Args:
        app: Flask application instance
    """
    # Phase 3.1: Player endpoints (8 endpoints)
    app.register_blueprint(player_bp)

    # Phase 3.2: Playlist endpoints (5 endpoints)
    app.register_blueprint(playlist_bp)

    # Phase 3.3: Media endpoints (2 endpoints)
    app.register_blueprint(media_bp)

    # Phase 3.4: Schedule endpoints (7 endpoints)
    app.register_blueprint(schedule_bp)

    # Phase 3.5: settings_bp (settings API endpoints)
    # Phase 3.6: auth_bp (auth routes)
