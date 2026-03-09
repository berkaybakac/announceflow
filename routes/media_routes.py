"""
AnnounceFlow - Media Routes
API endpoints for media file management (upload, delete).
"""
import os
import subprocess
import tempfile
import shutil
import threading
import time
from flask import Blueprint, request, redirect, url_for, flash, jsonify
from werkzeug.utils import secure_filename
import database as db
from logger import log_web
from utils.helpers import login_required, _flash_redirect
from services.config_service import load_config


media_bp = Blueprint("media", __name__)


# Media constants
MEDIA_FOLDER = str(load_config().get("media_folder", "media")).strip() or "media"
ALLOWED_EXTENSIONS = {"mp3", "wav", "ogg", "aiff", "aif", "flac", "m4a", "wma", "mp2", "opus"}
NEEDS_CONVERSION = {"wav", "ogg", "aiff", "aif", "flac", "m4a", "wma", "mp2", "opus"}
RECENT_UPLOAD_TTL_SECONDS = 15

os.makedirs(os.path.join(MEDIA_FOLDER, "music"), exist_ok=True)
os.makedirs(os.path.join(MEDIA_FOLDER, "announcements"), exist_ok=True)

_recent_upload_lock = threading.Lock()
_recent_uploads = {}  # {(filename_lower, media_type, size_bytes): last_seen_ts}


def allowed_file(filename):
    """Check if file extension is allowed."""
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


def _is_recent_duplicate_upload(
    filename: str, media_type: str, file_size: int, ttl_seconds: int = RECENT_UPLOAD_TTL_SECONDS
) -> bool:
    """Best-effort dedupe guard for rapid double-submit uploads."""
    now = time.time()
    key = (filename.lower(), media_type, int(file_size))
    with _recent_upload_lock:
        expired = [k for k, ts in _recent_uploads.items() if (now - ts) > ttl_seconds]
        for k in expired:
            _recent_uploads.pop(k, None)

        last_seen = _recent_uploads.get(key)
        _recent_uploads[key] = now
        return last_seen is not None and (now - last_seen) <= ttl_seconds


def convert_to_mp3(input_path: str, output_path: str) -> bool:
    """Convert any audio format to MP3 using ffmpeg."""
    import logging

    logger = logging.getLogger(__name__)
    try:
        result = subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-i",
                input_path,
                "-acodec",
                "libmp3lame",
                "-ab",
                "192k",
                "-ar",
                "44100",
                output_path,
            ],
            capture_output=True,
            text=True,
            timeout=120,
        )
        if result.returncode != 0:
            logger.error(f"ffmpeg conversion failed: {result.stderr}")
            return False
        return True
    except subprocess.TimeoutExpired:
        logger.error(f"ffmpeg conversion timeout (>120s): {input_path}")
        return False
    except Exception as e:
        logger.error(f"ffmpeg error: {e}")
        return False


def get_audio_duration(file_path: str) -> int:
    """Get audio duration in seconds using ffprobe."""
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "csv=p=0",
                file_path,
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode == 0 and result.stdout.strip():
            return int(float(result.stdout.strip()))
    except Exception:
        pass
    return 0


def has_audio_stream(file_path: str) -> bool:
    """Return True if ffprobe can detect at least one audio stream."""
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "a:0",
                "-show_entries",
                "stream=codec_name",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                file_path,
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        return result.returncode == 0 and bool(result.stdout.strip())
    except Exception:
        return False


def get_primary_audio_codec(file_path: str) -> str:
    """Return primary audio codec name from ffprobe (e.g., mp3, aac)."""
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "a:0",
                "-show_entries",
                "stream=codec_name",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                file_path,
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode == 0:
            return result.stdout.strip().lower()
    except Exception:
        pass
    return ""


@media_bp.route("/api/media/upload", methods=["POST"])
@login_required
def api_media_upload():
    """Upload a media file. Supports both traditional form POST and AJAX (X-Requested-With header)."""
    is_ajax = request.headers.get("X-Requested-With") == "XMLHttpRequest"

    def _err(msg, category="error"):
        if is_ajax:
            return jsonify({"success": False, "message": msg}), 400
        return _flash_redirect(msg, category, "library")

    if "file" not in request.files:
        return _err("Dosya seçilmedi")

    file = request.files["file"]
    media_type = request.form.get("media_type", "music")

    if media_type not in ("music", "announcement"):
        return _err("Geçersiz medya türü. 'music' veya 'announcement' olmalıdır.")

    if file.filename == "":
        return _err("Dosya seçilmedi")

    if file and file.filename and allowed_file(file.filename):
        original_filename = secure_filename(file.filename)
        subfolder = "music" if media_type == "music" else "announcements"

        # Get file extension
        base, ext = os.path.splitext(original_filename)
        ext_lower = ext.lower().lstrip(".")

        # Check if conversion is needed
        # Save everything to temp first, then decide direct save vs conversion
        temp_suffix = ext or ".tmp"
        with tempfile.NamedTemporaryFile(suffix=temp_suffix, delete=False) as tmp:
            temp_path = tmp.name
            file.save(temp_path)

        try:
            temp_size = os.path.getsize(temp_path)
            if _is_recent_duplicate_upload(original_filename, media_type, temp_size):
                return _err(
                    "Aynı dosya kısa sürede tekrar gönderildi. Çift tıklama engellendi.",
                    "warning",
                )

            if not has_audio_stream(temp_path):
                return _err("Yüklenen dosya bozuk veya ses akışı içermiyor.")

            codec = get_primary_audio_codec(temp_path)
            needs_convert = ext_lower in NEEDS_CONVERSION or codec != "mp3"

            if needs_convert:
                # Convert to canonical MP3 even when extension is .mp3 but codec is not mp3.
                mp3_filename = f"{base}.mp3"
                mp3_filepath = os.path.join(MEDIA_FOLDER, subfolder, mp3_filename)

                # Ensure unique filename
                counter = 1
                while os.path.exists(mp3_filepath):
                    mp3_filename = f"{base}_{counter}.mp3"
                    mp3_filepath = os.path.join(MEDIA_FOLDER, subfolder, mp3_filename)
                    counter += 1

                if not convert_to_mp3(temp_path, mp3_filepath):
                    return _err(f"{original_filename} dönüştürülemedi. ffmpeg hatası.")

                converted_codec = get_primary_audio_codec(mp3_filepath)
                if converted_codec != "mp3":
                    try:
                        os.remove(mp3_filepath)
                    except OSError:
                        pass
                    return _err(
                        f"{original_filename} dönüştürüldü ama MP3 doğrulaması başarısız."
                    )

                duration = get_audio_duration(mp3_filepath)
                db.add_media_file(mp3_filename, mp3_filepath, media_type, duration)
                log_web("upload", {"filename": mp3_filename, "media_type": media_type})
                success_msg = f"{original_filename} → {mp3_filename} dönüştürüldü ve yüklendi!"
                if not is_ajax:
                    flash(success_msg, "success")
            else:
                # True MP3: keep original file as-is.
                target_filename = original_filename
                filepath = os.path.join(MEDIA_FOLDER, subfolder, target_filename)

                counter = 1
                while os.path.exists(filepath):
                    target_filename = f"{base}_{counter}{ext}"
                    filepath = os.path.join(MEDIA_FOLDER, subfolder, target_filename)
                    counter += 1

                shutil.move(temp_path, filepath)
                temp_path = None

                duration = get_audio_duration(filepath)
                db.add_media_file(target_filename, filepath, media_type, duration)
                log_web("upload", {"filename": target_filename, "media_type": media_type})
                success_msg = f"{target_filename} başarıyla yüklendi!"
                if not is_ajax:
                    flash(success_msg, "success")
        finally:
            if temp_path and os.path.exists(temp_path):
                os.remove(temp_path)
    else:
        return _err(
            "Geçersiz dosya türü. Kabul edilen: MP3, WAV, OGG, AIFF, FLAC, M4A, WMA, MP2"
        )

    if is_ajax:
        return jsonify({"success": True}), 200
    return redirect(url_for("library"))


@media_bp.route("/api/media/<int:media_id>/delete", methods=["POST", "DELETE"])
@login_required
def api_media_delete(media_id):
    """Delete a media file."""
    media = db.get_media_file(media_id)

    if not media:
        return jsonify({"success": False, "message": "Dosya bulunamadı"}), 404

    # Delete file from disk
    try:
        if os.path.exists(media["filepath"]):
            os.remove(media["filepath"])
    except OSError:
        return (
            jsonify(
                {
                    "success": False,
                    "message": "Dosya diskten silinemedi. Daha sonra tekrar deneyin.",
                }
            ),
            500,
        )

    # Delete from database
    deleted = db.delete_media_file(media_id)
    if not deleted:
        return jsonify({"success": False, "message": "Dosya bulunamadı"}), 404

    log_web("delete", {"media_id": media_id, "filename": media["filename"]})
    return jsonify({"success": True, "message": "Dosya silindi"})


@media_bp.route("/api/media/delete-batch", methods=["POST"])
@login_required
def api_media_delete_batch():
    """Delete multiple media files by IDs."""
    data = request.get_json(silent=True) or {}
    raw_ids = data.get("ids", [])

    if not isinstance(raw_ids, list):
        return jsonify({"success": False, "message": "Geçersiz istek formatı"}), 400

    media_ids = []
    for item in raw_ids:
        try:
            media_id = int(item)
            if media_id > 0:
                media_ids.append(media_id)
        except (ValueError, TypeError):
            continue

    if not media_ids:
        return jsonify({"success": False, "message": "Silinecek dosya seçilmedi"}), 400

    unique_ids = sorted(set(media_ids))
    deleted_count = 0
    failed_count = 0
    not_found_count = 0

    for media_id in unique_ids:
        media = db.get_media_file(media_id)
        if not media:
            not_found_count += 1
            continue

        try:
            if os.path.exists(media["filepath"]):
                os.remove(media["filepath"])
        except OSError:
            failed_count += 1
            continue

        if db.delete_media_file(media_id):
            deleted_count += 1
            log_web("delete", {"media_id": media_id, "filename": media["filename"]})
        else:
            not_found_count += 1

    if deleted_count == 0:
        return (
            jsonify(
                {
                    "success": False,
                    "message": "Silinecek dosya bulunamadı",
                    "deleted_count": 0,
                    "failed_count": failed_count,
                    "not_found_count": not_found_count,
                }
            ),
            404,
        )

    return jsonify(
        {
            "success": True,
            "message": f"{deleted_count} dosya silindi",
            "deleted_count": deleted_count,
            "failed_count": failed_count,
            "not_found_count": not_found_count,
            "requested_count": len(unique_ids),
        }
    )
