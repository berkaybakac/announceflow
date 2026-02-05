"""
AnnounceFlow - Web Panel
Flask web server with API endpoints for management.
"""
import os
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
from services.config_service import load_config

app = Flask(__name__)

# Security: Read secret key from environment variable
# In production, set FLASK_SECRET_KEY environment variable
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "dev-only-change-in-production")
if app.secret_key == "dev-only-change-in-production":
    import logging

    logging.getLogger(__name__).warning(
        "Using default secret key! Set FLASK_SECRET_KEY environment variable in production."
    )

MEDIA_FOLDER = "media"

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

    # Get disk space (media folder)
    try:
        import shutil

        disk_usage = shutil.disk_usage(MEDIA_FOLDER)
        disk_free_mb = round(disk_usage.free / (1024 * 1024))
        disk_total_mb = round(disk_usage.total / (1024 * 1024))
    except OSError:
        disk_free_mb = 0
        disk_total_mb = 0

    return render_template(
        "library.html",
        active_page="library",
        music_files=music_fmt,
        announcement_files=announcements_fmt,
        total_files=total_files,
        total_size_mb=total_size_mb,
        total_duration_minutes=total_duration_minutes,
        disk_free_mb=disk_free_mb,
        disk_total_mb=disk_total_mb,
    )


def get_system_stats():
    """Get system stats (disk and memory) using standard libraries."""
    stats = {
        "disk_total_gb": 0.0,
        "disk_free_gb": 0.0,
        "disk_percent": 0.0,
        "ram_total_mb": 0,
        "ram_free_mb": 0,
        "estimated_songs": 0,
    }

    try:
        # Disk Usage
        total, used, free = shutil.disk_usage("/")
        stats["disk_total_gb"] = round(total / (1024**3), 1)
        stats["disk_free_gb"] = round(free / (1024**3), 1)
        stats["disk_percent"] = round((used / total) * 100, 1)

        # Estimate song capacity (avg 5MB per song)
        # Leave 1GB buffer for system
        available_for_media = max(0, free - (1024**3))
        stats["estimated_songs"] = int(available_for_media / (5 * 1024 * 1024))

        # RAM Usage (Linux specific)
        if os.path.exists("/proc/meminfo"):
            with open("/proc/meminfo", "r") as f:
                meminfo = {}
                for line in f:
                    parts = line.split(":")
                    if len(parts) == 2:
                        meminfo[parts[0].strip()] = int(parts[1].strip().split()[0])

            # Total RAM
            if "MemTotal" in meminfo:
                stats["ram_total_mb"] = round(meminfo["MemTotal"] / 1024, 0)

            # Available RAM
            if "MemAvailable" in meminfo:
                stats["ram_free_mb"] = round(meminfo["MemAvailable"] / 1024, 0)
    except Exception as e:
        print(f"Error getting system stats: {e}")

    return stats


@app.route("/settings")
@login_required
def settings():
    """Settings page."""
    import prayer_times as pt
    import logging

    logger = logging.getLogger(__name__)
    logger.info("Settings page requested")

    config = load_config()
    system_stats = get_system_stats()

    # Get statistics (kept simple for compatibility)
    music_count = len(db.get_all_media_files("music"))
    announcement_count = len(db.get_all_media_files("announcement"))
    pending_count = len(db.get_pending_one_time_schedules())
    active_recurring = len(db.get_active_recurring_schedules())

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
        total_music=music_count,
        total_announcements=announcement_count,
        total_schedules=pending_count + active_recurring,
        admin_username=config.get("admin_username", "admin"),
        # Working hours settings
        working_hours_enabled=config.get("working_hours_enabled", False),
        working_hours_start=config.get("working_hours_start", "09:00"),
        working_hours_end=config.get("working_hours_end", "18:00"),
        # Prayer times settings
        prayer_times_enabled=config.get("prayer_times_enabled", False),
        prayer_times_city=config.get("prayer_times_city", ""),
        prayer_times_district=config.get("prayer_times_district", ""),
        system_stats=system_stats,
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
    player = get_player()
    player.set_volume(initial_volume)
    db.update_playback_state(volume=initial_volume)

    # Start scheduler
    scheduler = get_scheduler()
    scheduler.start()

    # Run web server
    from waitress import serve

    print("AnnounceFlow Web Panel çalışıyor (Port 5000)...")
    # Increase threads to prevent queue depth warnings
    serve(
        app,
        host="0.0.0.0",
        port=5000,
        threads=16,
        channel_timeout=10,
        connection_limit=100,
    )
