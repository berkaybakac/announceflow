"""
AnnounceFlow Helpers
Reusable helper functions for routes.
"""
import functools
from flask import jsonify, flash, redirect, url_for, session
import database as db


def login_required(f):
    """Decorator to require login for a route."""

    @functools.wraps(f)
    def wrapped(*args, **kwargs):
        if "logged_in" not in session:
            return redirect(
                "/login"
            )  # Direct path, not url_for (avoid app context dependency)
        return f(*args, **kwargs)

    return wrapped


def _json_success(data=None, **kwargs):
    """Standard success JSON response."""
    result = {"success": True}
    if data:
        result.update(data)
    result.update(kwargs)
    return jsonify(result)


def _json_error(message, status=400):
    """Standard error JSON response."""
    return jsonify({"error": message}), status


def _flash_redirect(message, category, route):
    """Flash message and redirect to route."""
    flash(message, category)
    return redirect(url_for(route))


def _get_media_or_404(media_id):
    """Get media file or return 404 error.
    Returns: (media, None) on success, (None, error_response) on failure
    """
    media = db.get_media_file(media_id)
    if not media:
        return None, _json_error("Media not found", 404)
    return media, None
