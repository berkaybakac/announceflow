"""
Tests for:
  - /api/upcoming-schedules endpoint
  - /api/playlist/start-all concurrent lock
  - _play_mpg123 orphan process cleanup

Categories per section: happy | error | edge | security
"""
import os
import platform
import sqlite3
import tempfile
import threading
import time
import unittest
from unittest.mock import MagicMock, call, patch

import database as db
from database.media_repository import MediaRepository
from database.playback_repository import PlaybackRepository
from database.schedule_repository import ScheduleRepository
from player import get_player
from routes.playlist_routes import _start_all_lock
from web_panel import app


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _setup_test_db(tmpdir: str):
    path = os.path.join(tmpdir, "test.db")
    db.DATABASE_PATH = path
    db._media_repo = MediaRepository(path)
    db._schedule_repo = ScheduleRepository(path)
    db._playback_repo = PlaybackRepository(path)
    db.init_database()
    return path


def _add_media(filename: str, duration: int = 30, media_type: str = "announcement") -> int:
    return db.add_media_file(
        filename=filename,
        filepath=f"/tmp/{filename}",
        media_type=media_type,
        duration_seconds=duration,
    )


# ---------------------------------------------------------------------------
# /api/upcoming-schedules
# ---------------------------------------------------------------------------

class UpcomingSchedulesApiTest(unittest.TestCase):

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self._old = (
            db.DATABASE_PATH,
            db._media_repo,
            db._schedule_repo,
            db._playback_repo,
        )
        _setup_test_db(self._tmpdir.name)
        app.config["TESTING"] = True
        self.client = app.test_client()
        with self.client.session_transaction() as sess:
            sess["logged_in"] = True

    def tearDown(self):
        db.DATABASE_PATH, db._media_repo, db._schedule_repo, db._playback_repo = self._old
        self._tmpdir.cleanup()

    # --- happy ---

    def test_returns_empty_list_when_no_schedules(self):
        resp = self.client.get("/api/upcoming-schedules")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(data["schedules"], [])

    def test_returns_formatted_schedule(self):
        from datetime import datetime
        media_id = _add_media("jingle.mp3")
        db.add_one_time_schedule(media_id, datetime(2030, 6, 15, 9, 30))

        resp = self.client.get("/api/upcoming-schedules")
        self.assertEqual(resp.status_code, 200)
        schedules = resp.get_json()["schedules"]
        self.assertEqual(len(schedules), 1)
        self.assertEqual(schedules[0]["filename"], "jingle.mp3")
        self.assertIn("display_datetime", schedules[0])
        self.assertIn("blocked_outside_hours", schedules[0])

    def test_returns_multiple_schedules_ordered(self):
        from datetime import datetime
        m1 = _add_media("first.mp3")
        m2 = _add_media("second.mp3")
        db.add_one_time_schedule(m1, datetime(2030, 6, 15, 8, 0))
        db.add_one_time_schedule(m2, datetime(2030, 6, 15, 9, 0))

        schedules = self.client.get("/api/upcoming-schedules").get_json()["schedules"]
        self.assertEqual(len(schedules), 2)
        self.assertEqual(schedules[0]["filename"], "first.mp3")

    # --- error ---

    def test_db_error_propagates_as_unhandled(self):
        # Flask TESTING=True propagates unhandled exceptions (500 in production).
        # The route intentionally has no try/except: Flask's error handler covers it.
        with patch("database.get_pending_one_time_schedules", side_effect=RuntimeError("db down")):
            with self.assertRaises(RuntimeError):
                self.client.get("/api/upcoming-schedules")

    # --- edge ---

    def test_no_filepath_or_internal_ids_in_response(self):
        """Sensitive fields must not leak to the caller."""
        from datetime import datetime
        media_id = _add_media("secret.mp3")
        db.add_one_time_schedule(media_id, datetime(2030, 1, 1, 10, 0))

        schedules = self.client.get("/api/upcoming-schedules").get_json()["schedules"]
        row = schedules[0]
        for forbidden in ("filepath", "media_id", "id", "status"):
            self.assertNotIn(forbidden, row, f"Field '{forbidden}' must not appear in response")

    def test_blocked_outside_hours_flag_is_boolean(self):
        from datetime import datetime
        media_id = _add_media("flagged.mp3")
        db.add_one_time_schedule(media_id, datetime(2030, 1, 1, 10, 0))

        schedules = self.client.get("/api/upcoming-schedules").get_json()["schedules"]
        self.assertIsInstance(schedules[0]["blocked_outside_hours"], bool)

    def test_unparseable_datetime_falls_back_to_raw_string(self):
        row = {"scheduled_datetime": "INVALID", "filename": "x.mp3", "blocked_outside_hours": False}
        with patch("database.get_pending_one_time_schedules", return_value=[row]):
            schedules = self.client.get("/api/upcoming-schedules").get_json()["schedules"]
        self.assertEqual(schedules[0]["display_datetime"], "INVALID")

    # --- security ---

    def test_unauthenticated_request_is_rejected(self):
        unauthenticated = app.test_client()
        resp = unauthenticated.get("/api/upcoming-schedules")
        self.assertIn(resp.status_code, (302, 401))

    def test_response_never_contains_filepath(self):
        """Regression: full dict() exposure would leak file system paths."""
        from datetime import datetime
        media_id = _add_media("private.mp3")
        db.add_one_time_schedule(media_id, datetime(2030, 1, 1, 10, 0))

        body = self.client.get("/api/upcoming-schedules").data.decode()
        self.assertNotIn("/tmp/", body)


# ---------------------------------------------------------------------------
# /api/playlist/start-all concurrent lock
# ---------------------------------------------------------------------------

class StartAllLockTest(unittest.TestCase):

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self._old = (
            db.DATABASE_PATH,
            db._media_repo,
            db._schedule_repo,
            db._playback_repo,
        )
        _setup_test_db(self._tmpdir.name)
        app.config["TESTING"] = True
        self.client = app.test_client()
        with self.client.session_transaction() as sess:
            sess["logged_in"] = True

    def tearDown(self):
        db.DATABASE_PATH, db._media_repo, db._schedule_repo, db._playback_repo = self._old
        self._tmpdir.cleanup()
        # Ensure lock is released even if a test fails mid-hold
        if _start_all_lock.locked():
            try:
                _start_all_lock.release()
            except RuntimeError:
                pass

    # --- happy ---

    def test_first_request_processes_normally(self):
        _add_media("track.mp3", media_type="music")
        with (
            patch("routes.playlist_routes.get_player") as mock_player_factory,
        ):
            mock_player = MagicMock()
            mock_player.play_playlist.return_value = True
            mock_player_factory.return_value = mock_player

            resp = self.client.post("/api/playlist/start-all")

        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data["success"])

    # --- error ---

    def test_no_music_files_returns_404(self):
        resp = self.client.post("/api/playlist/start-all")
        self.assertEqual(resp.status_code, 404)

    # --- edge ---

    def test_concurrent_request_returns_busy_when_lock_held(self):
        """Second request while lock is held must get busy=True, not start a new play."""
        _start_all_lock.acquire()
        try:
            resp = self.client.post("/api/playlist/start-all")
        finally:
            _start_all_lock.release()

        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertFalse(data["success"])
        self.assertEqual(data["reason"], "busy")

    def test_lock_is_released_after_successful_request(self):
        """Lock must not remain held after a normal request completes."""
        _add_media("track.mp3", media_type="music")
        with patch("routes.playlist_routes.get_player") as mock_player_factory:
            mock_player = MagicMock()
            mock_player.play_playlist.return_value = True
            mock_player_factory.return_value = mock_player
            self.client.post("/api/playlist/start-all")

        self.assertFalse(_start_all_lock.locked())

    def test_lock_is_released_even_when_db_raises(self):
        """Lock must be released even if an internal error occurs."""
        with patch("routes.playlist_routes.db.get_all_media_files", side_effect=RuntimeError("db error")):
            try:
                self.client.post("/api/playlist/start-all")
            except RuntimeError:
                pass

        self.assertFalse(_start_all_lock.locked())

    # --- security ---

    def test_unauthenticated_cannot_start_playlist(self):
        unauthenticated = app.test_client()
        resp = unauthenticated.post("/api/playlist/start-all")
        self.assertIn(resp.status_code, (302, 401))


# ---------------------------------------------------------------------------
# _play_mpg123 orphan process cleanup
# ---------------------------------------------------------------------------

@unittest.skipIf(platform.system() != "Linux", "mpg123 backend only runs on Linux")
class OrphanProcessKillTest(unittest.TestCase):
    """Verify that _play_mpg123 kills any process left by a concurrent thread."""

    def _make_mock_proc(self, running: bool = True):
        proc = MagicMock()
        proc.poll.return_value = None if running else 0
        return proc

    # --- happy ---

    def test_orphan_process_is_killed_before_new_one_starts(self):
        player = get_player()
        orphan = self._make_mock_proc(running=True)
        player._process = orphan

        new_proc = self._make_mock_proc(running=True)
        with (
            patch("subprocess.Popen", return_value=new_proc),
            patch("time.sleep"),
            patch.object(player, "_start_monitor_mpg123"),
        ):
            player._play_mpg123("/fake/track.mp3", playback_session=99)

        orphan.kill.assert_called_once()

    def test_new_process_is_stored_after_orphan_killed(self):
        player = get_player()
        player._process = self._make_mock_proc()

        new_proc = self._make_mock_proc()
        with (
            patch("subprocess.Popen", return_value=new_proc),
            patch("time.sleep"),
            patch.object(player, "_start_monitor_mpg123"),
        ):
            player._play_mpg123("/fake/track.mp3", playback_session=99)

        self.assertIs(player._process, new_proc)

    # --- error ---

    def test_kill_failure_does_not_prevent_new_playback(self):
        """If kill() raises, _play_mpg123 must still start the new process."""
        player = get_player()
        orphan = self._make_mock_proc()
        orphan.kill.side_effect = ProcessLookupError("already gone")
        player._process = orphan

        new_proc = self._make_mock_proc()
        with (
            patch("subprocess.Popen", return_value=new_proc),
            patch("time.sleep"),
            patch.object(player, "_start_monitor_mpg123"),
        ):
            result = player._play_mpg123("/fake/track.mp3", playback_session=99)

        self.assertTrue(result)
        self.assertIs(player._process, new_proc)

    # --- edge ---

    def test_no_kill_when_process_is_none(self):
        """No kill attempt when there is no existing process."""
        player = get_player()
        player._process = None

        new_proc = self._make_mock_proc()
        with (
            patch("subprocess.Popen", return_value=new_proc),
            patch("time.sleep"),
            patch.object(player, "_start_monitor_mpg123"),
        ):
            player._play_mpg123("/fake/track.mp3", playback_session=99)

        # No kill called on None – the code must not crash
        self.assertIs(player._process, new_proc)

    def test_process_reference_cleared_even_when_kill_raises(self):
        """self._process must be None after a failed kill (finally block)."""
        player = get_player()
        orphan = self._make_mock_proc()
        orphan.kill.side_effect = PermissionError("no permission")
        player._process = orphan

        new_proc = self._make_mock_proc()
        with (
            patch("subprocess.Popen", return_value=new_proc),
            patch("time.sleep"),
            patch.object(player, "_start_monitor_mpg123"),
        ):
            player._play_mpg123("/fake/track.mp3", playback_session=99)

        # After the try/finally, old reference is gone
        self.assertIsNot(player._process, orphan)

    # --- security ---

    def test_orphan_kill_does_not_log_sensitive_path(self):
        """Kill debug log must not expose file paths from other requests."""
        player = get_player()
        orphan = self._make_mock_proc()
        orphan.kill.side_effect = OSError("oops")
        player._process = orphan

        new_proc = self._make_mock_proc()
        with (
            patch("subprocess.Popen", return_value=new_proc),
            patch("time.sleep"),
            patch.object(player, "_start_monitor_mpg123"),
            patch("player.logger") as mock_logger,
        ):
            player._play_mpg123("/secret/path/track.mp3", playback_session=99)

        # The debug log for the kill error must not include the new file's path
        for c in mock_logger.debug.call_args_list:
            if "Orphan" in str(c):
                self.assertNotIn("/secret/path/", str(c))


if __name__ == "__main__":
    unittest.main()
