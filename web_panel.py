"""
AnnounceFlow - Web Panel
Flask web server with API endpoints for management.
"""
import os
import logging
import secrets
import re
import json
import shutil
from datetime import datetime, timedelta
from flask import (
    Flask,
    render_template,
    request,
    redirect,
    url_for,
    flash,
    session,
    jsonify,
)

import database as db
from player import get_player
from scheduler import get_scheduler
from logger import log_web
from services.config_service import load_config, save_config, load_dotenv_if_present

app = Flask(__name__)
load_dotenv_if_present()

def _resolve_secret_key() -> str:
    """Resolve Flask secret key from env/config, else auto-generate and persist."""
    env_key = os.environ.get("FLASK_SECRET_KEY")
    if env_key:
        return env_key

    config = load_config()
    config_key = config.get("flask_secret_key")
    if isinstance(config_key, str) and len(config_key) >= 16:
        return config_key

    generated = secrets.token_hex(32)
    config["flask_secret_key"] = generated
    if save_config(config):
        logging.getLogger(__name__).info(
            "flask_secret_key otomatik üretildi ve config.json içine kaydedildi."
        )
    else:
        logging.getLogger(__name__).warning(
            "flask_secret_key üretildi ama config.json'a kaydedilemedi; "
            "bu açılış için geçici key kullanılacak."
        )

    logging.getLogger(__name__).warning(
        "FLASK_SECRET_KEY ayarlı değil; otomatik üretilen key kullanılıyor."
    )
    return generated


app.secret_key = _resolve_secret_key()

_boot_config = load_config()
MEDIA_FOLDER = str(_boot_config.get("media_folder", "media")).strip() or "media"

# Ensure media directories exist
os.makedirs(os.path.join(MEDIA_FOLDER, "music"), exist_ok=True)
os.makedirs(os.path.join(MEDIA_FOLDER, "announcements"), exist_ok=True)


# ============ HELPERS ============

# Helper functions moved to utils/helpers.py (avoid circular imports)
from utils.helpers import login_required


# ============ AUTH ROUTES ============


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username")
        password = request.form.get("password")

        config = load_config()
        valid_user = config.get("admin_username", "admin")
        valid_pass = config.get("admin_password", "admin123")

        if username == valid_user and password == valid_pass:
            session["logged_in"] = True
            log_web("login", {"username": username})
            return redirect(url_for("index"))
        else:
            flash("Hatalı kullanıcı adı veya şifre!", "error")

    return render_template("login.html")


@app.route("/logout")
def logout():
    session.pop("logged_in", None)
    return redirect(url_for("login"))


def _format_schedules(schedules):
    """Format schedule datetime for display (DD.MM.YYYY HH:MM)."""
    formatted = []
    for s in schedules:
        s_dict = dict(s)
        try:
            dt_str = s_dict["scheduled_datetime"]
            # Handle T separator if present
            dt_str = dt_str.replace("T", " ")
            # Parse (try with constraints)
            try:
                dt = datetime.strptime(dt_str, "%Y-%m-%d %H:%M:%S")
            except ValueError:
                dt = datetime.strptime(dt_str, "%Y-%m-%d %H:%M")

            s_dict["display_datetime"] = dt.strftime("%d.%m.%Y %H:%M")
        except Exception:
            s_dict["display_datetime"] = s_dict["scheduled_datetime"]
        formatted.append(s_dict)
    return formatted


def _format_media_files(files):
    """Format media file dates (UTC -> UTC+3) and (DD.MM.YYYY HH:MM)."""
    formatted = []
    for f in files:
        f_dict = dict(f)
        try:
            dt_str = f_dict["created_at"]
            # Parse UTC time
            try:
                dt = datetime.strptime(dt_str, "%Y-%m-%d %H:%M:%S")
            except ValueError:
                dt = datetime.strptime(dt_str, "%Y-%m-%d %H:%M")

            # Add 3 hours for Turkey Time (UTC+3) manual adjustment
            # since we know the server stores UTC
            dt_tr = dt + timedelta(hours=3)

            f_dict["created_at_formatted"] = dt_tr.strftime("%d.%m.%Y %H:%M")
        except Exception:
            f_dict["created_at_formatted"] = f_dict["created_at"]
        formatted.append(f_dict)
    return formatted


def _is_time_within_configured_hours(
    time_obj, enabled: bool, start_str: str, end_str: str
) -> bool:
    """Check if a time falls within configured working hours, including overnight."""
    if not enabled:
        return True
    try:
        st = datetime.strptime(start_str, "%H:%M").time()
        en = datetime.strptime(end_str, "%H:%M").time()
        if st <= en:
            return st <= time_obj <= en
        # Overnight window (e.g. 22:00 -> 06:00)
        return time_obj >= st or time_obj <= en
    except Exception:
        return True


# ============ PAGE ROUTES ============


@app.route("/")
@login_required
def index():
    """Now Playing page."""
    media_files = db.get_all_media_files()
    upcoming = db.get_pending_one_time_schedules()
    upcoming_formatted = _format_schedules(upcoming)
    config = load_config()
    working_hours_enabled = config.get("working_hours_enabled", False)
    work_start = config.get("working_hours_start", "09:00")
    work_end = config.get("working_hours_end", "22:00")

    for s in upcoming_formatted:
        blocked = False
        if working_hours_enabled:
            try:
                dt_str = s.get("scheduled_datetime", "").replace("T", " ")
                try:
                    scheduled_dt = datetime.strptime(dt_str, "%Y-%m-%d %H:%M:%S")
                except ValueError:
                    scheduled_dt = datetime.strptime(dt_str, "%Y-%m-%d %H:%M")
                blocked = not _is_time_within_configured_hours(
                    scheduled_dt.time(), working_hours_enabled, work_start, work_end
                )
            except Exception:
                blocked = False
        s["blocked_outside_hours"] = blocked
    return render_template(
        "index.html",
        active_page="now-playing",
        media_files=media_files,
        upcoming_schedules=upcoming_formatted,
    )


@app.route("/schedules/one-time")
@login_required
def one_time_schedules():
    """One-time schedules page."""
    media_files = db.get_all_media_files()
    schedules = db.get_all_one_time_schedules()
    schedules_formatted = _format_schedules(schedules)
    config = load_config()
    working_hours_enabled = config.get("working_hours_enabled", False)
    work_start = config.get("working_hours_start", "09:00")
    work_end = config.get("working_hours_end", "22:00")

    for s in schedules_formatted:
        blocked = False
        if working_hours_enabled:
            try:
                dt_str = s.get("scheduled_datetime", "").replace("T", " ")
                try:
                    scheduled_dt = datetime.strptime(dt_str, "%Y-%m-%d %H:%M:%S")
                except ValueError:
                    scheduled_dt = datetime.strptime(dt_str, "%Y-%m-%d %H:%M")
                blocked = not _is_time_within_configured_hours(
                    scheduled_dt.time(), working_hours_enabled, work_start, work_end
                )
            except Exception:
                blocked = False
        s["blocked_outside_hours"] = blocked
    return render_template(
        "one_time_schedule.html",
        active_page="one-time",
        media_files=media_files,
        schedules=schedules_formatted,
        working_hours_enabled=working_hours_enabled,
        working_hours_start=work_start,
        working_hours_end=work_end,
    )


@app.route("/schedules/recurring")
@login_required
def recurring_schedules():
    """Recurring schedules page."""
    media_files = db.get_all_media_files()
    schedules = db.get_all_recurring_schedules()
    return render_template(
        "recurring_schedule.html",
        active_page="recurring",
        media_files=media_files,
        schedules=schedules,
    )


@app.route("/library")
@login_required
def library():
    """Media library page."""
    music_files = db.get_all_media_files("music")
    announcement_files = db.get_all_media_files("announcement")

    music_fmt = _format_media_files(music_files)
    announcements_fmt = _format_media_files(announcement_files)
    total_music_files = len(music_files)
    total_announcement_files = len(announcement_files)
    total_active_plans = len(db.get_pending_one_time_schedules()) + len(
        db.get_active_recurring_schedules()
    )

    # Calculate storage statistics
    total_files = len(music_files) + len(announcement_files)
    all_files = list(music_files) + list(announcement_files)

    # Optimized: use os.stat() for single syscall per file (instead of exists + getsize)
    total_size_bytes = 0
    for f in all_files:
        try:
            total_size_bytes += os.stat(f["filepath"]).st_size
        except (OSError, FileNotFoundError):
            pass  # File doesn't exist or inaccessible

    total_duration_seconds = sum(f.get("duration_seconds", 0) for f in all_files)

    # Format for display
    total_size_mb = round(total_size_bytes / (1024 * 1024), 1)
    total_duration_minutes = round(total_duration_seconds / 60)

    # Capacity metrics (single source of truth: media storage location)
    disk_free_bytes = 0
    disk_free_gb = 0.0
    fallback_track_bytes = 5 * 1024 * 1024  # 5 MB fallback
    average_track_size_bytes = (
        int(total_size_bytes / total_files)
        if total_files > 0 and total_size_bytes > 0
        else fallback_track_bytes
    )
    average_track_size_mb = round(average_track_size_bytes / (1024 * 1024), 1)

    try:
        disk_usage = shutil.disk_usage(MEDIA_FOLDER)
        disk_free_bytes = disk_usage.free
        disk_free_gb = round(disk_free_bytes / (1024**3), 1)
    except OSError:
        disk_free_bytes = 0
        disk_free_gb = 0.0

    # Leave 1GB buffer for system before estimating additional track capacity
    estimated_track_capacity = int(
        max(0, disk_free_bytes - (1024**3)) / max(average_track_size_bytes, 1)
    )

    return render_template(
        "library.html",
        active_page="library",
        music_files=music_fmt,
        announcement_files=announcements_fmt,
        total_music_files=total_music_files,
        total_announcement_files=total_announcement_files,
        total_active_plans=total_active_plans,
        total_files=total_files,
        total_size_mb=total_size_mb,
        total_duration_minutes=total_duration_minutes,
        disk_free_gb=disk_free_gb,
        estimated_track_capacity=estimated_track_capacity,
        average_track_size_mb=average_track_size_mb,
    )


@app.route("/settings")
@login_required
def settings():
    """Settings page."""
    import prayer_times as pt
    import logging

    logger = logging.getLogger(__name__)
    logger.debug("Settings page requested")

    config = load_config()
    # Get cities (fast, cached)
    cities = pt.get_cities()

    # Get next prayer time if enabled
    next_prayer = None
    prayer_city = config.get("prayer_times_city", "")
    prayer_district = config.get("prayer_times_district", "")
    if config.get("prayer_times_enabled") and prayer_city:
        next_prayer = pt.get_next_prayer_time(prayer_city, prayer_district)

    return render_template(
        "settings.html",
        active_page="settings",
        volume=get_player().get_volume(),
        admin_username=config.get("admin_username", "admin"),
        # Working hours settings
        working_hours_enabled=config.get("working_hours_enabled", False),
        working_hours_start=config.get("working_hours_start", "09:00"),
        working_hours_end=config.get("working_hours_end", "18:00"),
        # Prayer times settings
        prayer_times_enabled=config.get("prayer_times_enabled", False),
        prayer_times_city=config.get("prayer_times_city", ""),
        prayer_times_district=config.get("prayer_times_district", ""),
        cities=cities,
        districts_json="{}",  # Now loaded via AJAX
        next_prayer=next_prayer,
    )


# ============ PLAYER API ============
# Phase 3.1: All player endpoints moved to routes/player_routes.py
# - 3.1a: /api/health, /api/play, /api/stop, /api/volume
# - 3.1b: /api/now-playing, /api/media/music
# - 3.1c: /api/pause, /api/resume (deprecated)


# ============ PLAYLIST API ============
# Phase 3.2: All playlist endpoints moved to routes/playlist_routes.py


# ============ MEDIA API ============
# Phase 3.3: All media endpoints moved to routes/media_routes.py


# ============ SCHEDULE API ============
# Phase 3.4: All schedule endpoints moved to routes/schedule_routes.py


# ============ SETTINGS API ============
# Phase 3.5: All settings endpoints moved to routes/settings_routes.py


# ============ BLUEPRINT REGISTRATION ============

from routes import register_blueprints

register_blueprints(app)


# ============ MAIN ============

if __name__ == "__main__":
    # Initialize database
    db.init_database()

    # Initialize volume from config
    config = load_config()
    initial_volume = config.get("volume", 80)
    try:
        web_port = int(config.get("web_port", 5001))
        if web_port < 1 or web_port > 65535:
            raise ValueError("out of range")
    except (TypeError, ValueError):
        web_port = 5001

    player = get_player()
    player.set_volume(initial_volume)
    db.update_playback_state(volume=initial_volume)

    # Start scheduler
    scheduler = get_scheduler()
    scheduler.start()

    # Run web server
    from waitress import serve

    print(f"AnnounceFlow Web Panel çalışıyor (Port {web_port})...")
    # Increase threads to prevent queue depth warnings
    serve(
        app,
        host="0.0.0.0",
        port=web_port,
        threads=16,
        channel_timeout=10,
        connection_limit=100,
    )
