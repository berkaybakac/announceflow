"""
AnnounceFlow - Media Routes
API endpoints for media file management (upload, delete).
"""
import os
import subprocess
import tempfile
from flask import Blueprint, request, redirect, url_for, flash
from werkzeug.utils import secure_filename
import database as db
from logger import log_web
from utils.helpers import login_required, _flash_redirect


media_bp = Blueprint("media", __name__)


# Media constants
MEDIA_FOLDER = "media"
ALLOWED_EXTENSIONS = {"mp3", "wav", "ogg", "aiff", "aif", "flac", "m4a", "wma", "mp2"}
NEEDS_CONVERSION = {"wav", "ogg", "aiff", "aif", "flac", "m4a", "wma", "mp2"}


def allowed_file(filename):
    """Check if file extension is allowed."""
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


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


@media_bp.route("/api/media/upload", methods=["POST"])
@login_required
def api_media_upload():
    """Upload a media file."""
    if "file" not in request.files:
        return _flash_redirect("Dosya seçilmedi", "error", "library")

    file = request.files["file"]
    media_type = request.form.get("media_type", "music")

    if file.filename == "":
        return _flash_redirect("Dosya seçilmedi", "error", "library")

    if file and file.filename and allowed_file(file.filename):
        original_filename = secure_filename(file.filename)
        subfolder = "music" if media_type == "music" else "announcements"

        # Get file extension
        base, ext = os.path.splitext(original_filename)
        ext_lower = ext.lower().lstrip(".")

        # Check if conversion is needed
        needs_convert = ext_lower in NEEDS_CONVERSION

        if needs_convert:
            # Save to temp file with unique name (avoids collision)
            temp_suffix = ext  # Keep original extension
            with tempfile.NamedTemporaryFile(suffix=temp_suffix, delete=False) as tmp:
                temp_path = tmp.name
                file.save(temp_path)

            # Create MP3 filename
            mp3_filename = f"{base}.mp3"
            mp3_filepath = os.path.join(MEDIA_FOLDER, subfolder, mp3_filename)

            # Ensure unique filename
            counter = 1
            while os.path.exists(mp3_filepath):
                mp3_filename = f"{base}_{counter}.mp3"
                mp3_filepath = os.path.join(MEDIA_FOLDER, subfolder, mp3_filename)
                counter += 1

            # Convert to MP3
            try:
                if convert_to_mp3(temp_path, mp3_filepath):
                    # Get duration from converted file
                    duration = get_audio_duration(mp3_filepath)
                    db.add_media_file(mp3_filename, mp3_filepath, media_type, duration)
                    flash(
                        f"{original_filename} → {mp3_filename} dönüştürüldü ve yüklendi!",
                        "success",
                    )
                else:
                    flash(
                        f"{original_filename} dönüştürülemedi. ffmpeg hatası.", "error"
                    )
            finally:
                # Always clean up temp file
                if os.path.exists(temp_path):
                    os.remove(temp_path)
        else:
            # MP3 - save directly
            filepath = os.path.join(MEDIA_FOLDER, subfolder, original_filename)

            # Ensure unique filename
            counter = 1
            while os.path.exists(filepath):
                original_filename = f"{base}_{counter}{ext}"
                filepath = os.path.join(MEDIA_FOLDER, subfolder, original_filename)
                counter += 1

            file.save(filepath)
            # Get duration
            duration = get_audio_duration(filepath)
            db.add_media_file(original_filename, filepath, media_type, duration)
            log_web("upload", {"filename": original_filename, "media_type": media_type})
            flash(f"{original_filename} başarıyla yüklendi!", "success")
    else:
        return _flash_redirect(
            "Geçersiz dosya türü. Kabul edilen: MP3, WAV, OGG, AIFF, FLAC, M4A, WMA, MP2",
            "error",
            "library",
        )

    return redirect(url_for("library"))


@media_bp.route("/api/media/<int:media_id>/delete", methods=["POST"])
@login_required
def api_media_delete(media_id):
    """Delete a media file."""
    media = db.get_media_file(media_id)

    if media:
        # Delete file from disk
        if os.path.exists(media["filepath"]):
            os.remove(media["filepath"])

        # Delete from database
        db.delete_media_file(media_id)
        log_web("delete", {"media_id": media_id, "filename": media["filename"]})
        return _flash_redirect("Dosya silindi", "success", "library")
    else:
        return _flash_redirect("Dosya bulunamadı", "error", "library")
