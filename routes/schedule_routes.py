"""
AnnounceFlow - Schedule Routes
API endpoints for schedule management (one-time and recurring).
"""
import json
import re
from datetime import datetime
from flask import Blueprint, request, redirect, url_for, jsonify
import database as db
from services.schedule_conflict_service import (
    find_conflict_for_one_time,
    find_conflict_for_recurring,
    has_self_overlap_for_interval,
    resolve_duration_seconds,
)
from services.slot_map_service import get_day_slots, get_week_slots
from utils.helpers import login_required, _flash_redirect


schedule_bp = Blueprint("schedule", __name__)
SCHEDULE_CONFLICT_MESSAGE = "Seçtiğiniz süreyi kapsayan başka bir plan vardır."


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

    try:
        media_id_int = int(media_id)
    except (ValueError, TypeError):
        return _flash_redirect("Geçersiz dosya seçimi", "error", "one_time_schedules")

    try:
        scheduled_dt = datetime.strptime(f"{date} {time}", "%Y-%m-%d %H:%M")
    except ValueError:
        return _flash_redirect(
            "Geçersiz tarih/saat formatı", "error", "one_time_schedules"
        )

    if scheduled_dt <= datetime.now():
        return _flash_redirect(
            "Geçmiş bir tarih seçemezsiniz", "error", "one_time_schedules"
        )

    conflict = find_conflict_for_one_time(scheduled_dt, media_id_int)
    if conflict:
        return _flash_redirect(
            SCHEDULE_CONFLICT_MESSAGE, "error", "one_time_schedules"
        )

    db.add_one_time_schedule(media_id_int, scheduled_dt, reason)
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
    deleted = db.delete_one_time_schedule(schedule_id)
    if not deleted:
        return jsonify({"success": False, "message": "Plan bulunamadı"}), 404
    return jsonify({"success": True, "message": "Plan silindi"})


@schedule_bp.route("/api/schedules/one-time/delete-batch", methods=["POST"])
@login_required
def api_delete_one_time_batch():
    """Delete multiple one-time schedules by IDs."""
    data = request.get_json(silent=True) or {}
    raw_ids = data.get("ids", [])

    if not isinstance(raw_ids, list):
        return jsonify({"success": False, "message": "Geçersiz istek formatı"}), 400

    schedule_ids = []
    for item in raw_ids:
        try:
            schedule_id = int(item)
            if schedule_id > 0:
                schedule_ids.append(schedule_id)
        except (ValueError, TypeError):
            continue

    if not schedule_ids:
        return jsonify({"success": False, "message": "Silinecek plan seçilmedi"}), 400

    unique_ids = sorted(set(schedule_ids))
    deleted_count = db.delete_one_time_schedules(unique_ids)

    if deleted_count == 0:
        return jsonify({"success": False, "message": "Silinecek plan bulunamadı"}), 404

    return jsonify(
        {
            "success": True,
            "message": f"{deleted_count} plan silindi",
            "deleted_count": deleted_count,
            "requested_count": len(unique_ids),
        }
    )


@schedule_bp.route("/api/schedules/recurring", methods=["POST"])
@login_required
def api_add_recurring():
    """Add a recurring schedule."""
    media_id = request.form.get("media_id")
    days_json = request.form.get("days_of_week", "[]")
    schedule_type = request.form.get("schedule_type", "specific")
    reason = request.form.get("reason", "").strip() or None

    try:
        days = json.loads(days_json)
    except (json.JSONDecodeError, ValueError):
        days = []

    if not media_id or not days:
        return _flash_redirect(
            "Dosya ve günler gerekli", "error", "recurring_schedules"
        )

    try:
        media_id_int = int(media_id)
    except (ValueError, TypeError):
        return _flash_redirect("Geçersiz dosya seçimi", "error", "recurring_schedules")

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

        candidate = {
            "media_id": media_id_int,
            "days_of_week": days,
            "specific_times": times,
            "schedule_type": "specific",
        }
        conflict = find_conflict_for_recurring(candidate)
        if conflict:
            return _flash_redirect(
                SCHEDULE_CONFLICT_MESSAGE, "error", "recurring_schedules"
            )

        db.add_recurring_schedule(
            media_id_int,
            days,
            times[0],
            specific_times=times,  # First time as start
            reason=reason,
        )
    else:
        start_time = request.form.get("start_time", "09:00")
        end_time = request.form.get("end_time", "18:00")
        interval_raw = request.form.get("interval_minutes", "60")

        try:
            interval = int(interval_raw)
        except (ValueError, TypeError):
            return _flash_redirect(
                "Zaman aralığı sayı olmalıdır", "error", "recurring_schedules"
            )

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

        duration_seconds = resolve_duration_seconds(media_id_int)
        if has_self_overlap_for_interval(duration_seconds, interval):
            return _flash_redirect(
                SCHEDULE_CONFLICT_MESSAGE, "error", "recurring_schedules"
            )

        candidate = {
            "media_id": media_id_int,
            "days_of_week": days,
            "start_time": start_time,
            "end_time": end_time,
            "interval_minutes": interval,
            "schedule_type": "interval",
        }
        conflict = find_conflict_for_recurring(candidate)
        if conflict:
            return _flash_redirect(
                SCHEDULE_CONFLICT_MESSAGE, "error", "recurring_schedules"
            )

        db.add_recurring_schedule(
            media_id_int, days, start_time, end_time, interval, reason=reason
        )

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
        if new_state:
            media_id = int(current["media_id"])
            is_interval = not bool(current.get("specific_times"))
            if is_interval:
                duration_seconds = resolve_duration_seconds(media_id)
                interval_minutes = int(current.get("interval_minutes") or 0)
                if has_self_overlap_for_interval(duration_seconds, interval_minutes):
                    return _flash_redirect(
                        SCHEDULE_CONFLICT_MESSAGE, "error", "recurring_schedules"
                    )

            candidate = {
                "media_id": media_id,
                "days_of_week": current.get("days_of_week", []),
                "specific_times": current.get("specific_times"),
                "start_time": current.get("start_time"),
                "end_time": current.get("end_time"),
                "interval_minutes": current.get("interval_minutes", 0),
                "schedule_type": "specific"
                if current.get("specific_times")
                else "interval",
            }
            conflict = find_conflict_for_recurring(
                candidate, exclude_recurring_id=schedule_id
            )
            if conflict:
                return _flash_redirect(
                    SCHEDULE_CONFLICT_MESSAGE, "error", "recurring_schedules"
                )

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
    deleted = db.delete_recurring_schedule(schedule_id)
    if not deleted:
        return jsonify({"success": False, "message": "Plan bulunamadı"}), 404
    return jsonify({"success": True, "message": "Plan silindi"})


@schedule_bp.route("/api/schedules/recurring/delete-batch", methods=["POST"])
@login_required
def api_delete_recurring_batch():
    """Delete multiple recurring schedules by IDs."""
    data = request.get_json(silent=True) or {}
    raw_ids = data.get("ids", [])

    if not isinstance(raw_ids, list):
        return jsonify({"success": False, "message": "Geçersiz istek formatı"}), 400

    schedule_ids = []
    for item in raw_ids:
        try:
            schedule_id = int(item)
            if schedule_id > 0:
                schedule_ids.append(schedule_id)
        except (ValueError, TypeError):
            continue

    if not schedule_ids:
        return jsonify({"success": False, "message": "Silinecek plan seçilmedi"}), 400

    unique_ids = sorted(set(schedule_ids))
    deleted_count = db.delete_recurring_schedules(unique_ids)

    if deleted_count == 0:
        return jsonify({"success": False, "message": "Silinecek plan bulunamadı"}), 404

    return jsonify(
        {
            "success": True,
            "message": f"{deleted_count} plan silindi",
            "deleted_count": deleted_count,
            "requested_count": len(unique_ids),
        }
    )


@schedule_bp.route("/api/schedules/day-slots", methods=["GET"])
@login_required
def api_day_slots():
    """Return occupied time slots for a specific date."""
    date_str = request.args.get("date", "")
    if not date_str:
        date_str = datetime.now().strftime("%Y-%m-%d")
    return jsonify(get_day_slots(date_str))


@schedule_bp.route("/api/schedules/week-slots", methods=["GET"])
@login_required
def api_week_slots():
    """Return occupied time slots for a full week."""
    date_str = request.args.get("date", "")
    return jsonify(get_week_slots(date_str or None))


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
