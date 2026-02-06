"""
AnnounceFlow - Settings Routes
API endpoints for settings management (credentials, working hours, prayer times).
"""
import logging
import re
from flask import Blueprint, request, jsonify
from services.config_service import load_config, save_config
from utils.helpers import login_required, _flash_redirect


settings_bp = Blueprint("settings", __name__)

logger = logging.getLogger(__name__)


def _is_valid_hhmm(value: str) -> bool:
    """Validate HH:MM (24h) format."""
    if not isinstance(value, str):
        return False
    return bool(re.match(r"^([01]\d|2[0-3]):([0-5]\d)$", value))


@settings_bp.route("/api/settings/credentials", methods=["POST"])
@login_required
def api_update_credentials():
    """Update admin credentials."""
    config = load_config()

    username = request.form.get("username")
    password = request.form.get("password")
    password_confirm = request.form.get("password_confirm")

    if username and username != config.get("admin_username"):
        logger.info(
            f"Admin username changed from {config.get('admin_username')} to {username}"
        )
        config["admin_username"] = username

    if password:
        # Validate password confirmation
        if password != password_confirm:
            return _flash_redirect("Şifreler eşleşmiyor!", "error", "settings")

        logger.info("Admin password changed")
        config["admin_password"] = password

    save_config(config)
    return _flash_redirect("Yönetici bilgileri güncellendi", "success", "settings")


@settings_bp.route("/api/prayer-times/districts")
@login_required
def api_get_districts():
    """Get districts for a city."""
    city = request.args.get("city")
    if not city:
        return jsonify([])

    import prayer_times as pt

    districts = pt.get_districts(city)
    return jsonify(districts)


@settings_bp.route("/api/settings/working-hours", methods=["POST"])
@login_required
def api_update_working_hours():
    """Update working hours settings."""
    config = load_config()
    start = request.form.get("working_hours_start", "09:00").strip()
    end = request.form.get("working_hours_end", "22:00").strip()

    if not _is_valid_hhmm(start) or not _is_valid_hhmm(end):
        return _flash_redirect(
            "Saat formatı geçersiz (Doğru format: HH:MM, örn: 09:00)",
            "error",
            "settings",
        )

    # Checkbox sends '1' when checked, missing when unchecked
    config["working_hours_enabled"] = "working_hours_enabled" in request.form
    config["working_hours_start"] = start
    config["working_hours_end"] = end

    save_config(config)
    return _flash_redirect(
        "Çalışma saatleri ayarları güncellendi", "success", "settings"
    )


@settings_bp.route("/api/settings/prayer-times", methods=["POST"])
@login_required
def api_update_prayer_times():
    """Update prayer times settings."""
    config = load_config()

    city = request.form.get("prayer_times_city", "")
    district = request.form.get("prayer_times_district", "")

    # Validate: if city is selected, district is required
    if city and not district:
        return _flash_redirect("İl seçiliyken ilçe zorunludur!", "error", "settings")

    config["prayer_times_enabled"] = "prayer_times_enabled" in request.form
    config["prayer_times_city"] = city
    config["prayer_times_district"] = district

    save_config(config)
    return _flash_redirect("Ezan vakitleri ayarları güncellendi", "success", "settings")
