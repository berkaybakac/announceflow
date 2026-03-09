"""Tests for the media upload endpoint (AJAX multi-file support).

Covers:
- AJAX mode (X-Requested-With: XMLHttpRequest) → JSON responses
- Traditional form POST mode → redirect responses
- All validation/error paths (no happy-path tunnel vision)
- Auth guard
- Duplicate upload detection (15-second TTL)
- File type routing (music vs announcement subfolder)
- Filename collision handling (counter suffix)
- All allowed extensions
- MP3 file with non-MP3 codec triggers conversion
- Database record creation on success
"""
from __future__ import annotations

import io
import os
import tempfile
import unittest
from unittest.mock import patch

import database as db
from database.media_repository import MediaRepository
from database.playback_repository import PlaybackRepository
from database.schedule_repository import ScheduleRepository
import routes.media_routes as media_routes_module
from web_panel import app


# ─── Helpers ──────────────────────────────────────────────────────────────────

_MP3_STUB = b"ID3" + b"\x00" * 120   # plausible MP3 header stub
_WAV_STUB = b"RIFF" + b"\x00" * 120  # plausible RIFF stub


def _fake_convert_ok(src: str, dst: str) -> bool:
    """Side-effect for convert_to_mp3 that actually creates the output file."""
    with open(dst, "wb") as f:
        f.write(b"converted mp3 stub")
    return True


# ─── Base test class ──────────────────────────────────────────────────────────

class _UploadTestBase(unittest.TestCase):
    """Shared setUp / tearDown: isolated DB + isolated media folder + Flask client."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self._media_dir = os.path.join(self._tmpdir.name, "media")
        os.makedirs(os.path.join(self._media_dir, "music"), exist_ok=True)
        os.makedirs(os.path.join(self._media_dir, "announcements"), exist_ok=True)

        # --- Isolated DB ---
        self._test_db_path = os.path.join(self._tmpdir.name, "test.db")
        self._orig = {
            "DB_PATH": db.DATABASE_PATH,
            "media_repo": db._media_repo,
            "schedule_repo": db._schedule_repo,
            "playback_repo": db._playback_repo,
        }
        db.DATABASE_PATH = self._test_db_path
        db._media_repo = MediaRepository(self._test_db_path)
        db._schedule_repo = ScheduleRepository(self._test_db_path)
        db._playback_repo = PlaybackRepository(self._test_db_path)
        db.init_database()

        # --- Patch MEDIA_FOLDER so files land in temp dir ---
        self._mf_patcher = patch.object(media_routes_module, "MEDIA_FOLDER", self._media_dir)
        self._mf_patcher.start()

        # --- Clear duplicate-upload cache between tests ---
        with media_routes_module._recent_upload_lock:
            media_routes_module._recent_uploads.clear()

        # --- Flask test client ---
        app.config["TESTING"] = True
        self.client = app.test_client()
        self._set_logged_in(True)

    def tearDown(self):
        self._mf_patcher.stop()
        db.DATABASE_PATH = self._orig["DB_PATH"]
        db._media_repo = self._orig["media_repo"]
        db._schedule_repo = self._orig["schedule_repo"]
        db._playback_repo = self._orig["playback_repo"]
        self._tmpdir.cleanup()

    # ── Helpers ──────────────────────────────────────────────────────────────

    def _set_logged_in(self, value: bool):
        with self.client.session_transaction() as sess:
            if value:
                sess["logged_in"] = True
            else:
                sess.pop("logged_in", None)

    def _upload(
        self,
        filename: str = "test.mp3",
        content: bytes = _MP3_STUB,
        media_type: str = "music",
        ajax: bool = True,
    ):
        """POST to /api/media/upload and return the Response."""
        headers = {"X-Requested-With": "XMLHttpRequest"} if ajax else {}
        return self.client.post(
            "/api/media/upload",
            data={
                "file": (io.BytesIO(content), filename),
                "media_type": media_type,
            },
            content_type="multipart/form-data",
            headers=headers,
        )

    def _clear_dedup(self):
        with media_routes_module._recent_upload_lock:
            media_routes_module._recent_uploads.clear()


# ─── AJAX success paths ───────────────────────────────────────────────────────

class TestUploadAjaxSuccess(_UploadTestBase):
    """AJAX upload → JSON 200 {"success": True}."""

    @patch("routes.media_routes.has_audio_stream", return_value=True)
    @patch("routes.media_routes.get_primary_audio_codec", return_value="mp3")
    @patch("routes.media_routes.get_audio_duration", return_value=180)
    def test_valid_mp3_returns_json_success(self, _dur, _codec, _has):
        resp = self._upload("sarki.mp3")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data["success"])

    @patch("routes.media_routes.has_audio_stream", return_value=True)
    @patch("routes.media_routes.get_primary_audio_codec", return_value="mp3")
    @patch("routes.media_routes.get_audio_duration", return_value=30)
    def test_announcement_media_type_routes_correctly(self, _dur, _codec, _has):
        resp = self._upload("anons.mp3", media_type="announcement")
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.get_json()["success"])
        self.assertTrue(
            os.path.exists(os.path.join(self._media_dir, "announcements", "anons.mp3"))
        )

    @patch("routes.media_routes.has_audio_stream", return_value=True)
    @patch("routes.media_routes.get_audio_duration", return_value=120)
    def test_wav_file_converted_to_mp3_ajax_success(self, _dur, _has):
        """WAV → conversion path → JSON 200 on success."""
        # codec: first call returns "wav" (input), second returns "mp3" (output)
        with patch(
            "routes.media_routes.get_primary_audio_codec", side_effect=["wav", "mp3"]
        ), patch("routes.media_routes.convert_to_mp3", side_effect=_fake_convert_ok):
            resp = self._upload("song.wav", content=_WAV_STUB)
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.get_json()["success"])

    @patch("routes.media_routes.has_audio_stream", return_value=True)
    @patch("routes.media_routes.get_audio_duration", return_value=120)
    def test_mp3_file_with_non_mp3_codec_triggers_conversion(self, _dur, _has):
        """MP3 extension but codec=aac → treated as needs conversion."""
        with patch(
            "routes.media_routes.get_primary_audio_codec", side_effect=["aac", "mp3"]
        ), patch("routes.media_routes.convert_to_mp3", side_effect=_fake_convert_ok):
            resp = self._upload("weird.mp3")
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.get_json()["success"])

    @patch("routes.media_routes.has_audio_stream", return_value=True)
    @patch("routes.media_routes.get_primary_audio_codec", return_value="mp3")
    @patch("routes.media_routes.get_audio_duration", return_value=200)
    def test_filename_collision_gets_unique_counter_suffix(self, _dur, _codec, _has):
        """When target file already exists, _1 is appended."""
        # Pre-create the collision
        existing = os.path.join(self._media_dir, "music", "track.mp3")
        with open(existing, "wb") as f:
            f.write(b"existing")

        resp = self._upload("track.mp3")
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.get_json()["success"])
        self.assertTrue(os.path.exists(os.path.join(self._media_dir, "music", "track_1.mp3")))

    @patch("routes.media_routes.has_audio_stream", return_value=True)
    @patch("routes.media_routes.get_primary_audio_codec", return_value="mp3")
    @patch("routes.media_routes.get_audio_duration", return_value=180)
    def test_successful_upload_creates_db_record(self, _dur, _codec, _has):
        """After upload, media_files table has the new record."""
        resp = self._upload("dbtest.mp3")
        self.assertEqual(resp.status_code, 200)
        files = db.get_all_media_files("music")
        self.assertTrue(any(f["filename"] == "dbtest.mp3" for f in files))

    @patch("routes.media_routes.has_audio_stream", return_value=True)
    @patch("routes.media_routes.get_primary_audio_codec", return_value="mp3")
    @patch("routes.media_routes.get_audio_duration", return_value=180)
    def test_multiple_sequential_uploads_all_succeed(self, _dur, _codec, _has):
        """Simulates the batch upload JS loop: N sequential requests all succeed."""
        filenames = [f"track_{i}.mp3" for i in range(5)]
        for name in filenames:
            resp = self._upload(name)
            self.assertEqual(resp.status_code, 200, f"{name} failed: {resp.get_json()}")
            self.assertTrue(resp.get_json()["success"])
        files = db.get_all_media_files("music")
        uploaded = {f["filename"] for f in files}
        for name in filenames:
            self.assertIn(name, uploaded)


# ─── AJAX error paths ─────────────────────────────────────────────────────────

class TestUploadAjaxErrors(_UploadTestBase):
    """Every error path should return JSON 400 {"success": False}."""

    def _assert_error(self, resp, fragment: str | None = None):
        self.assertEqual(resp.status_code, 400)
        data = resp.get_json()
        self.assertIsNotNone(data, "Expected JSON response body")
        self.assertFalse(data["success"])
        if fragment:
            self.assertIn(fragment, data.get("message", ""))

    # ── Missing / empty file ──────────────────────────────────────────────────

    def test_no_file_field_in_request(self):
        resp = self.client.post(
            "/api/media/upload",
            data={"media_type": "music"},
            content_type="multipart/form-data",
            headers={"X-Requested-With": "XMLHttpRequest"},
        )
        self._assert_error(resp, "seçilmedi")

    def test_empty_filename(self):
        resp = self.client.post(
            "/api/media/upload",
            data={"file": (io.BytesIO(b""), ""), "media_type": "music"},
            content_type="multipart/form-data",
            headers={"X-Requested-With": "XMLHttpRequest"},
        )
        self._assert_error(resp, "seçilmedi")

    # ── Disallowed extensions ─────────────────────────────────────────────────

    def test_txt_extension_rejected(self):
        resp = self._upload("notaudio.txt", content=b"hello world")
        self._assert_error(resp, "Geçersiz")

    def test_exe_extension_rejected(self):
        resp = self._upload("malware.exe", content=b"MZ\x90\x00")
        self._assert_error(resp, "Geçersiz")

    def test_mp4_extension_rejected(self):
        resp = self._upload("video.mp4", content=b"\x00\x01\x02")
        self._assert_error(resp, "Geçersiz")

    def test_no_extension_rejected(self):
        resp = self._upload("noextension", content=b"data")
        self._assert_error(resp, "Geçersiz")

    def test_double_extension_trick_rejected(self):
        """audio.mp3.exe should be treated as .exe → rejected."""
        resp = self._upload("audio.mp3.exe", content=b"MZ")
        self._assert_error(resp, "Geçersiz")

    # ── Audio stream validation ───────────────────────────────────────────────

    @patch("routes.media_routes.has_audio_stream", return_value=False)
    def test_corrupt_file_no_audio_stream(self, _has):
        """Valid extension but ffprobe finds no audio stream."""
        resp = self._upload("corrupt.mp3")
        self._assert_error(resp)
        data = resp.get_json()
        self.assertTrue(
            "bozuk" in data["message"] or "ses akışı" in data["message"]
        )

    @patch("routes.media_routes.has_audio_stream", return_value=False)
    def test_wav_with_no_audio_stream_rejected(self, _has):
        resp = self._upload("empty.wav", content=_WAV_STUB)
        self._assert_error(resp)

    # ── FFmpeg conversion failures ────────────────────────────────────────────

    @patch("routes.media_routes.has_audio_stream", return_value=True)
    @patch("routes.media_routes.get_primary_audio_codec", return_value="wav")
    @patch("routes.media_routes.convert_to_mp3", return_value=False)
    def test_ffmpeg_conversion_fails(self, _conv, _codec, _has):
        resp = self._upload("bad.wav", content=_WAV_STUB)
        self._assert_error(resp, "dönüştürülemedi")

    @patch("routes.media_routes.has_audio_stream", return_value=True)
    @patch("routes.media_routes.get_audio_duration", return_value=60)
    def test_converted_file_has_wrong_codec(self, _dur, _has):
        """Conversion ran but output codec ≠ mp3 → error."""
        with patch(
            "routes.media_routes.get_primary_audio_codec", side_effect=["flac", "aac"]
        ), patch("routes.media_routes.convert_to_mp3", side_effect=_fake_convert_ok):
            resp = self._upload("bad_convert.flac", content=_WAV_STUB)
        self._assert_error(resp, "doğrulaması")

    # ── Duplicate detection ───────────────────────────────────────────────────

    @patch("routes.media_routes.has_audio_stream", return_value=True)
    @patch("routes.media_routes.get_primary_audio_codec", return_value="mp3")
    @patch("routes.media_routes.get_audio_duration", return_value=180)
    def test_first_upload_succeeds_second_within_ttl_rejected(self, _dur, _codec, _has):
        content = b"X" * 512
        r1 = self._upload("dup.mp3", content=content)
        self.assertEqual(r1.status_code, 200)
        self.assertTrue(r1.get_json()["success"])

        # Same (filename, media_type, size) within 15 s
        r2 = self._upload("dup.mp3", content=content)
        self._assert_error(r2)
        data = r2.get_json()
        self.assertTrue("tekrar" in data["message"] or "engellendi" in data["message"])

    @patch("routes.media_routes.has_audio_stream", return_value=True)
    @patch("routes.media_routes.get_primary_audio_codec", return_value="mp3")
    @patch("routes.media_routes.get_audio_duration", return_value=180)
    def test_different_size_not_flagged_as_duplicate(self, _dur, _codec, _has):
        """Same name but different byte-size is NOT a duplicate."""
        self._upload("song.mp3", content=b"A" * 100)
        self._clear_dedup()  # just cleared: simulate time passing
        # Different content = different size key
        r2 = self._upload("song.mp3", content=b"B" * 200)
        self.assertEqual(r2.status_code, 200)
        self.assertTrue(r2.get_json()["success"])

    @patch("routes.media_routes.has_audio_stream", return_value=True)
    @patch("routes.media_routes.get_primary_audio_codec", return_value="mp3")
    @patch("routes.media_routes.get_audio_duration", return_value=180)
    def test_duplicate_check_is_per_media_type(self, _dur, _codec, _has):
        """Same file name+size but different media_type → NOT a duplicate."""
        content = b"C" * 300
        r1 = self._upload("hello.mp3", content=content, media_type="music")
        self.assertEqual(r1.status_code, 200)

        # Same file to 'announcement' → different dedup key
        r2 = self._upload("hello.mp3", content=content, media_type="announcement")
        self.assertEqual(r2.status_code, 200)
        self.assertTrue(r2.get_json()["success"])


# ─── Traditional form POST (no AJAX header) ───────────────────────────────────

class TestUploadTraditionalForm(_UploadTestBase):
    """Without X-Requested-With, responses are redirects, not JSON."""

    @patch("routes.media_routes.has_audio_stream", return_value=True)
    @patch("routes.media_routes.get_primary_audio_codec", return_value="mp3")
    @patch("routes.media_routes.get_audio_duration", return_value=180)
    def test_successful_upload_returns_302_to_library(self, _dur, _codec, _has):
        resp = self._upload("form_track.mp3", ajax=False)
        self.assertEqual(resp.status_code, 302)
        self.assertIn("library", resp.headers.get("Location", ""))

    def test_invalid_extension_returns_302_not_json(self):
        resp = self._upload("invalid.txt", content=b"hello", ajax=False)
        self.assertEqual(resp.status_code, 302)
        self.assertIsNone(resp.get_json())

    def test_no_file_returns_302_not_json(self):
        resp = self.client.post(
            "/api/media/upload",
            data={"media_type": "music"},
            content_type="multipart/form-data",
            # No X-Requested-With header
        )
        self.assertEqual(resp.status_code, 302)
        self.assertIsNone(resp.get_json())

    @patch("routes.media_routes.has_audio_stream", return_value=False)
    def test_corrupt_file_form_post_returns_302(self, _has):
        resp = self._upload("corrupt.mp3", ajax=False)
        self.assertEqual(resp.status_code, 302)
        self.assertIsNone(resp.get_json())


# ─── Auth guard ───────────────────────────────────────────────────────────────

class TestUploadAuth(_UploadTestBase):
    """Unauthenticated requests must be redirected regardless of AJAX header."""

    def setUp(self):
        super().setUp()
        self._set_logged_in(False)

    def test_unauthenticated_ajax_request_redirected(self):
        resp = self.client.post(
            "/api/media/upload",
            data={"file": (io.BytesIO(b"data"), "test.mp3"), "media_type": "music"},
            content_type="multipart/form-data",
            headers={"X-Requested-With": "XMLHttpRequest"},
        )
        self.assertEqual(resp.status_code, 302)

    def test_unauthenticated_form_post_redirected(self):
        resp = self.client.post(
            "/api/media/upload",
            data={"file": (io.BytesIO(b"data"), "test.mp3"), "media_type": "music"},
            content_type="multipart/form-data",
        )
        self.assertEqual(resp.status_code, 302)


# ─── File routing by media_type ───────────────────────────────────────────────

class TestUploadFileRouting(_UploadTestBase):
    """Files must land in the correct subfolder."""

    @patch("routes.media_routes.has_audio_stream", return_value=True)
    @patch("routes.media_routes.get_primary_audio_codec", return_value="mp3")
    @patch("routes.media_routes.get_audio_duration", return_value=180)
    def test_music_type_goes_to_music_folder(self, _dur, _codec, _has):
        self._upload("bgm.mp3", media_type="music")
        self.assertTrue(os.path.exists(os.path.join(self._media_dir, "music", "bgm.mp3")))
        self.assertFalse(os.path.exists(os.path.join(self._media_dir, "announcements", "bgm.mp3")))

    @patch("routes.media_routes.has_audio_stream", return_value=True)
    @patch("routes.media_routes.get_primary_audio_codec", return_value="mp3")
    @patch("routes.media_routes.get_audio_duration", return_value=20)
    def test_announcement_type_goes_to_announcements_folder(self, _dur, _codec, _has):
        self._upload("welcome.mp3", media_type="announcement")
        self.assertTrue(
            os.path.exists(os.path.join(self._media_dir, "announcements", "welcome.mp3"))
        )
        self.assertFalse(os.path.exists(os.path.join(self._media_dir, "music", "welcome.mp3")))

    def test_unknown_media_type_returns_error(self):
        """media_type not in {'music', 'announcement'} → AJAX 400, form 302."""
        resp = self._upload("misc.mp3", media_type="unknown_value", ajax=True)
        self.assertEqual(resp.status_code, 400)
        data = resp.get_json()
        self.assertFalse(data["success"])
        self.assertIn("medya türü", data["message"])

    def test_unknown_media_type_form_post_returns_redirect(self):
        """Same validation via form POST returns redirect."""
        resp = self._upload("misc.mp3", media_type="unknown_value", ajax=False)
        self.assertEqual(resp.status_code, 302)


# ─── Allowed extension matrix ─────────────────────────────────────────────────

class TestUploadAllowedExtensions(_UploadTestBase):
    """Every extension in ALLOWED_EXTENSIONS must be accepted (AJAX 200)."""

    @patch("routes.media_routes.has_audio_stream", return_value=True)
    @patch("routes.media_routes.get_audio_duration", return_value=180)
    def test_all_allowed_extensions_accepted(self, _dur, _has):
        # (filename, input_codec, post_convert_codec)
        cases = [
            ("test.mp3",  "mp3",       None),          # direct, no conversion
            ("test.wav",  "pcm_s16le", "mp3"),         # conversion needed
            ("test.ogg",  "vorbis",    "mp3"),
            ("test.flac", "flac",      "mp3"),
            ("test.m4a",  "aac",       "mp3"),
            ("test.wma",  "wmav2",     "mp3"),
            ("test.aiff", "pcm_s16be", "mp3"),
            ("test.aif",  "pcm_s16be", "mp3"),
            ("test.mp2",  "mp2",       "mp3"),
            ("test.opus", "opus",      "mp3"),         # WhatsApp Android audio
        ]

        for filename, input_codec, output_codec in cases:
            with self.subTest(filename=filename):
                self._clear_dedup()

                if output_codec is None:
                    # True MP3 — no conversion
                    with patch(
                        "routes.media_routes.get_primary_audio_codec",
                        return_value=input_codec,
                    ):
                        resp = self._upload(filename, content=b"stub bytes")
                else:
                    with patch(
                        "routes.media_routes.get_primary_audio_codec",
                        side_effect=[input_codec, output_codec],
                    ), patch(
                        "routes.media_routes.convert_to_mp3",
                        side_effect=_fake_convert_ok,
                    ):
                        resp = self._upload(filename, content=b"stub bytes")

                self.assertEqual(
                    resp.status_code, 200,
                    f"{filename}: expected 200, got {resp.status_code} — {resp.get_json()}",
                )
                data = resp.get_json()
                self.assertTrue(data["success"], f"{filename}: {data}")


# ─── Edge: temp file cleanup ──────────────────────────────────────────────────

class TestUploadTempCleanup(_UploadTestBase):
    """Temp files must not be left behind after errors."""

    @patch("routes.media_routes.has_audio_stream", return_value=False)
    def test_no_orphaned_temp_files_on_audio_stream_error(self, _has):
        tmp_before = set(os.listdir(tempfile.gettempdir()))
        self._upload("corrupt.mp3")
        tmp_after = set(os.listdir(tempfile.gettempdir()))
        # Any .mp3 or .tmp files added during this test should be cleaned up.
        new_files = tmp_after - tmp_before
        mp3_leftovers = [f for f in new_files if f.endswith((".mp3", ".tmp"))]
        self.assertEqual(
            mp3_leftovers, [],
            f"Orphaned temp files left: {mp3_leftovers}",
        )

    @patch("routes.media_routes.has_audio_stream", return_value=True)
    @patch("routes.media_routes.get_primary_audio_codec", return_value="wav")
    @patch("routes.media_routes.convert_to_mp3", return_value=False)
    def test_no_orphaned_temp_files_on_conversion_error(self, _conv, _codec, _has):
        tmp_before = set(os.listdir(tempfile.gettempdir()))
        self._upload("bad.wav", content=_WAV_STUB)
        tmp_after = set(os.listdir(tempfile.gettempdir()))
        new_files = tmp_after - tmp_before
        wav_leftovers = [f for f in new_files if f.endswith((".wav", ".tmp"))]
        self.assertEqual(wav_leftovers, [], f"Orphaned temp files: {wav_leftovers}")


if __name__ == "__main__":
    unittest.main()
