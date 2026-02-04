"""
AnnounceFlow - Schedule Routes
API endpoints for schedule management (one-time and recurring).
"""
import json
import re
from datetime import datetime
from flask import Blueprint, request, redirect, url_for, jsonify
import database as db
from utils.helpers import login_required, _flash_redirect


schedule_bp = Blueprint("schedule", __name__)


def validate_time_format(time_str: str) -> bool:
    """Validate HH:MM time format."""
    pattern = r"^([01]?[0-9]|2[0-3]):([0-5][0-9])$"
    return bool(re.match(pattern, time_str))


@schedule_bp.route("/api/schedules/one-time", methods=["POST"])
@login_required
def api_add_one_time():
    """Add a one-time schedule."""
    media_id = request.form.get("media_id", "")
    date = request.form.get("date", "")
    time = request.form.get("time", "")
    reason = request.form.get("reason", "").strip() or None

    if not all([media_id, date, time]):
        return _flash_redirect("Tüm alanları doldurun", "error", "one_time_schedules")

    scheduled_dt = datetime.strptime(f"{date} {time}", "%Y-%m-%d %H:%M")

    if scheduled_dt <= datetime.now():
        return _flash_redirect(
            "Geçmiş bir tarih seçemezsiniz", "error", "one_time_schedules"
        )

    db.add_one_time_schedule(int(media_id), scheduled_dt, reason)
    return _flash_redirect("Plan başarıyla eklendi!", "success", "one_time_schedules")


@schedule_bp.route("/api/schedules/one-time/<int:schedule_id>/cancel", methods=["POST"])
@login_required
def api_cancel_one_time(schedule_id):
    """Cancel a one-time schedule."""
    db.update_one_time_schedule_status(schedule_id, "cancelled")
    return jsonify({"success": True, "message": "Plan iptal edildi"})


@schedule_bp.route("/api/schedules/one-time/<int:schedule_id>/delete", methods=["POST", "DELETE"])
@login_required
def api_delete_one_time(schedule_id):
    """Delete a one-time schedule."""
    db.delete_one_time_schedule(schedule_id)
    return jsonify({"success": True, "message": "Plan silindi"})


@schedule_bp.route("/api/schedules/recurring", methods=["POST"])
@login_required
def api_add_recurring():
    """Add a recurring schedule."""
    media_id = request.form.get("media_id")
    days_json = request.form.get("days_of_week", "[]")
    schedule_type = request.form.get("schedule_type", "specific")

    try:
        days = json.loads(days_json)
    except (json.JSONDecodeError, ValueError):
        days = []

    if not media_id or not days:
        return _flash_redirect(
            "Dosya ve günler gerekli", "error", "recurring_schedules"
        )

    if schedule_type == "specific":
        times_str = request.form.get("specific_times", "")
        times = [t.strip() for t in times_str.split(",") if t.strip()]

        if not times:
            return _flash_redirect(
                "En az bir saat girin", "error", "recurring_schedules"
            )

        # Validate time format (HH:MM)
        invalid_times = [t for t in times if not validate_time_format(t)]
        if invalid_times:
            return _flash_redirect(
                f'Geçersiz saat formatı: {", ".join(invalid_times)} (Doğru format: Saat:Dakika, örn: 09:00)',
                "error",
                "recurring_schedules",
            )

        db.add_recurring_schedule(
            int(media_id), days, times[0], specific_times=times  # First time as start
        )
    else:
        start_time = request.form.get("start_time", "09:00")
        end_time = request.form.get("end_time", "18:00")
        interval = int(request.form.get("interval_minutes", 60))

        # Validate time formats
        if not validate_time_format(start_time) or not validate_time_format(end_time):
            return _flash_redirect(
                "Geçersiz saat formatı (Doğru format: Saat:Dakika, örn: 09:00)",
                "error",
                "recurring_schedules",
            )

        # Backend validation: minimum interval is 1 minute
        if interval < 1:
            return _flash_redirect(
                "Zaman aralığı en az 1 dakika olmalıdır", "error", "recurring_schedules"
            )

        db.add_recurring_schedule(int(media_id), days, start_time, end_time, interval)

    return _flash_redirect(
        "Tekrarlı plan oluşturuldu!", "success", "recurring_schedules"
    )


@schedule_bp.route(
    "/api/schedules/recurring/<int:schedule_id>/toggle", methods=["POST"]
)
@login_required
def api_toggle_recurring(schedule_id):
    """Toggle a recurring schedule active state."""
    schedules = db.get_all_recurring_schedules()
    current = next((s for s in schedules if s["id"] == schedule_id), None)

    if current:
        new_state = not current["is_active"]
        db.toggle_recurring_schedule(schedule_id, new_state)
        return _flash_redirect(
            "Plan durumu güncellendi", "success", "recurring_schedules"
        )

    return redirect(url_for("recurring_schedules"))


@schedule_bp.route(
    "/api/schedules/recurring/<int:schedule_id>/delete", methods=["POST", "DELETE"]
)
@login_required
def api_delete_recurring(schedule_id):
    """Delete a recurring schedule."""
    db.delete_recurring_schedule(schedule_id)
    return jsonify({"success": True, "message": "Plan silindi"})


@schedule_bp.route(
    "/api/schedules/recurring/delete-all-announcements", methods=["POST"]
)
@login_required
def api_delete_all_recurring_announcements():
    """Delete all recurring announcement schedules."""
    deleted_count = db.delete_all_recurring_announcements()
    return _flash_redirect(
        f"{deleted_count} tekrarlı anons planı silindi",
        "success",
        "recurring_schedules",
    )
