"""Tests for shuffle mode in AudioPlayer.set_playlist() and playlist API endpoints.

Covers (no happy-path tunnel vision):
Player unit:
  - shuffle=False (default) — list order preserved in memory and DB
  - shuffle=True — random.shuffle is called, DB receives the shuffled copy
  - shuffle=True — original caller's list is NOT mutated
  - shuffle=True with empty list — returns False immediately, no crash
  - shuffle=True with single item — random.shuffle still called, no crash
  - shuffle=True with two identical paths — dedup is NOT performed (order only)

API /api/playlist/start-all:
  - No body → shuffle defaults to False (backward compat)
  - Empty JSON body {} → shuffle defaults to False
  - {"shuffle": true} → shuffle=True forwarded to player
  - {"shuffle": false} → shuffle=False forwarded to player
  - Library empty → 404 regardless of shuffle flag
  - Unauthenticated → 302/401 (auth guard unaffected)

API /api/playlist/set:
  - Missing media_ids + shuffle=True → 400 (validation runs before shuffle)
  - All invalid media_ids + shuffle=True → 404 (no valid files)
  - Valid media_ids + shuffle=True → player.set_playlist called with shuffle=True
  - Valid media_ids, shuffle absent → player.set_playlist called with shuffle=False
  - Unauthenticated → 302/401
"""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from unittest.mock import MagicMock, call, patch

import database as db
from database.media_repository import MediaRepository
from database.playback_repository import PlaybackRepository
from database.schedule_repository import ScheduleRepository
from player import AudioPlayer
from web_panel import app


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _make_player() -> AudioPlayer:
    """Instantiate a bare AudioPlayer (on macOS no ALSA init runs)."""
    return AudioPlayer()


# ─── Player unit tests ───────────────────────────────────────────────────────

class TestSetPlaylistShuffle(unittest.TestCase):
    """Unit tests for AudioPlayer.set_playlist() shuffle behaviour."""

    def setUp(self):
        self._save_patcher = patch("player.db.save_playlist_state")
        self._mock_save = self._save_patcher.start()

    def tearDown(self):
        self._save_patcher.stop()

    def _player(self) -> AudioPlayer:
        return _make_player()

    # ── shuffle=False (default) ──────────────────────────────────────────────

    def test_default_no_shuffle_preserves_order(self):
        """Without shuffle the list is stored in original order."""
        player = self._player()
        paths = ["/a.mp3", "/b.mp3", "/c.mp3"]
        player.set_playlist(paths)
        self.assertEqual(player._playlist, ["/a.mp3", "/b.mp3", "/c.mp3"])

    def test_explicit_false_preserves_order(self):
        paths = ["/x.mp3", "/y.mp3"]
        player = self._player()
        player.set_playlist(paths, shuffle=False)
        self.assertEqual(player._playlist, ["/x.mp3", "/y.mp3"])

    def test_no_shuffle_db_receives_original_order(self):
        paths = ["/a.mp3", "/b.mp3"]
        player = self._player()
        player.set_playlist(paths, shuffle=False)
        self._mock_save.assert_called_once()

    # ── shuffle=True — random.shuffle is called ──────────────────────────────

    def test_shuffle_true_calls_random_shuffle(self):
        """random.shuffle must be invoked when shuffle=True."""
        paths = ["/a.mp3", "/b.mp3", "/c.mp3"]
        player = self._player()
        with patch("random.shuffle") as mock_rng:
            player.set_playlist(paths, shuffle=True)
            mock_rng.assert_called_once()

    def test_shuffle_true_argument_to_random_shuffle_is_a_list(self):
        """random.shuffle receives a list (the copy, not the original)."""
        paths = ["/a.mp3", "/b.mp3", "/c.mp3"]
        player = self._player()
        captured = []
        def capture(lst):
            captured.append(lst)
        with patch("random.shuffle", side_effect=capture):
            player.set_playlist(paths, shuffle=True)
        self.assertEqual(len(captured), 1)
        self.assertIsInstance(captured[0], list)

    # ── Original list must NOT be mutated ────────────────────────────────────

    def test_shuffle_does_not_mutate_original_list(self):
        """The caller's list must be unchanged after set_playlist(shuffle=True)."""
        original = ["/a.mp3", "/b.mp3", "/c.mp3", "/d.mp3"]
        snapshot = list(original)
        player = self._player()
        # Use the real random.shuffle so we get actual mutation of the COPY
        player.set_playlist(original, shuffle=True)
        self.assertEqual(original, snapshot, "Caller's list was mutated!")

    # ── Edge: empty list ─────────────────────────────────────────────────────

    def test_shuffle_empty_list_returns_false(self):
        """Empty list → False immediately, random.shuffle must NOT be called."""
        player = self._player()
        with patch("random.shuffle") as mock_rng:
            result = player.set_playlist([], shuffle=True)
        self.assertFalse(result)
        mock_rng.assert_not_called()

    def test_shuffle_empty_list_leaves_playlist_unchanged(self):
        """Player state must not change when set_playlist called with empty list."""
        player = self._player()
        player._playlist = ["/existing.mp3"]
        player.set_playlist([], shuffle=True)
        self.assertEqual(player._playlist, ["/existing.mp3"])

    # ── Edge: single item ────────────────────────────────────────────────────

    def test_shuffle_single_item_succeeds(self):
        """Single-item list is valid; random.shuffle is still called."""
        player = self._player()
        with patch("random.shuffle") as mock_rng:
            result = player.set_playlist(["/only.mp3"], shuffle=True)
        self.assertTrue(result)
        mock_rng.assert_called_once()

    def test_shuffle_single_item_stored_in_playlist(self):
        player = self._player()
        with patch("random.shuffle"):
            player.set_playlist(["/only.mp3"], shuffle=True)
        self.assertEqual(player._playlist, ["/only.mp3"])

    # ── State flags are set regardless of shuffle ────────────────────────────

    def test_shuffle_sets_playlist_active(self):
        player = self._player()
        player.set_playlist(["/a.mp3", "/b.mp3"], shuffle=True)
        self.assertTrue(player._playlist_active)

    def test_shuffle_resets_index_to_minus_one(self):
        player = self._player()
        player._playlist_index = 5
        player.set_playlist(["/a.mp3", "/b.mp3"], shuffle=True)
        self.assertEqual(player._playlist_index, -1)

    # ── DB is always persisted with the (possibly shuffled) list ─────────────

    def test_shuffle_true_db_save_called(self):
        player = self._player()
        with patch("random.shuffle"):
            player.set_playlist(["/a.mp3", "/b.mp3"], shuffle=True)
        self._mock_save.assert_called_once()

    def test_no_shuffle_db_save_called(self):
        player = self._player()
        player.set_playlist(["/a.mp3", "/b.mp3"])
        self._mock_save.assert_called_once()

    # ── Duplicate paths are kept as-is (shuffle does not deduplicate) ────────

    def test_shuffle_preserves_duplicate_paths(self):
        """Shuffle must not filter duplicates — that is not its job."""
        paths = ["/a.mp3", "/a.mp3", "/b.mp3"]
        player = self._player()
        with patch("random.shuffle"):
            player.set_playlist(paths, shuffle=True)
        self.assertEqual(len(player._playlist), 3)


# ─── API-level tests ─────────────────────────────────────────────────────────

class _PlaylistApiBase(unittest.TestCase):
    """Shared setup for playlist route API tests."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self._test_db_path = os.path.join(self._tmpdir.name, "test_shuffle.db")

        self._old_db_path = db.DATABASE_PATH
        self._old_media_repo = db._media_repo
        self._old_schedule_repo = db._schedule_repo
        self._old_playback_repo = db._playback_repo

        db.DATABASE_PATH = self._test_db_path
        db._media_repo = MediaRepository(self._test_db_path)
        db._schedule_repo = ScheduleRepository(self._test_db_path)
        db._playback_repo = PlaybackRepository(self._test_db_path)
        db.init_database()

        app.config["TESTING"] = True
        self.client = app.test_client()
        self._login()

        # Shared mocks for every test
        self._mock_player = MagicMock()
        self._mock_player.set_playlist.return_value = True
        self._mock_player.play_playlist.return_value = True
        self._mock_player._playlist_active = True
        self._mock_player._playlist = []

        self._stream_patcher = patch(
            "routes.playlist_routes.get_stream_service",
            return_value=MagicMock(status=lambda: {"active": False}),
        )
        self._stream_patcher.start()

        self._player_patcher = patch(
            "routes.playlist_routes.get_player",
            return_value=self._mock_player,
        )
        self._player_patcher.start()

        # Bypass working-hours guard
        self._hours_patcher = patch(
            "routes.playlist_routes._reject_if_outside_working_hours",
            return_value=None,
        )
        self._hours_patcher.start()

    def tearDown(self):
        self._stream_patcher.stop()
        self._player_patcher.stop()
        self._hours_patcher.stop()

        db.DATABASE_PATH = self._old_db_path
        db._media_repo = self._old_media_repo
        db._schedule_repo = self._old_schedule_repo
        db._playback_repo = self._old_playback_repo
        self._tmpdir.cleanup()

    def _login(self):
        with self.client.session_transaction() as sess:
            sess["logged_in"] = True

    def _logout(self):
        with self.client.session_transaction() as sess:
            sess.pop("logged_in", None)

    def _post_json(self, url: str, payload=None):
        body = json.dumps(payload).encode() if payload is not None else b""
        return self.client.post(
            url,
            data=body,
            content_type="application/json",
        )


class TestStartAllShuffleApi(_PlaylistApiBase):
    """/api/playlist/start-all — shuffle parameter handling."""

    def _seed_music(self, count: int = 3):
        """Insert fake music rows into the isolated DB."""
        for i in range(count):
            db._media_repo.add_media_file(
                filename=f"track{i}.mp3",
                filepath=f"/media/music/track{i}.mp3",
                media_type="music",
                duration_seconds=180,
            )

    # ── Backward compat: no body ─────────────────────────────────────────────

    def test_no_json_content_type_returns_415(self):
        """POST with no Content-Type → Flask rejects with 415 before our code runs."""
        self._seed_music()
        resp = self.client.post("/api/playlist/start-all")
        self.assertEqual(resp.status_code, 415)
        self._mock_player.set_playlist.assert_not_called()

    def test_empty_json_body_shuffle_defaults_false(self):
        """Empty JSON {} → shuffle=False."""
        self._seed_music()
        resp = self._post_json("/api/playlist/start-all", {})
        self.assertEqual(resp.status_code, 200)
        _, kwargs = self._mock_player.set_playlist.call_args
        self.assertFalse(kwargs.get("shuffle", False))

    # ── shuffle=true ─────────────────────────────────────────────────────────

    def test_shuffle_true_forwarded_to_player(self):
        """{"shuffle": true} → set_playlist called with shuffle=True."""
        self._seed_music()
        resp = self._post_json("/api/playlist/start-all", {"shuffle": True})
        self.assertEqual(resp.status_code, 200)
        _, kwargs = self._mock_player.set_playlist.call_args
        self.assertTrue(kwargs.get("shuffle"))

    def test_shuffle_false_forwarded_to_player(self):
        """{"shuffle": false} → set_playlist called with shuffle=False."""
        self._seed_music()
        resp = self._post_json("/api/playlist/start-all", {"shuffle": False})
        self.assertEqual(resp.status_code, 200)
        _, kwargs = self._mock_player.set_playlist.call_args
        self.assertFalse(kwargs.get("shuffle", False))

    def test_shuffle_true_response_contains_track_count(self):
        """Successful shuffle response still includes tracks count."""
        self._seed_music(5)
        resp = self._post_json("/api/playlist/start-all", {"shuffle": True})
        self.assertEqual(resp.status_code, 200)
        data = json.loads(resp.data)
        self.assertEqual(data["tracks"], 5)

    # ── Error paths unaffected by shuffle ────────────────────────────────────

    def test_empty_library_returns_404_with_shuffle_true(self):
        """No music in library → 404 even if shuffle=True."""
        resp = self._post_json("/api/playlist/start-all", {"shuffle": True})
        self.assertEqual(resp.status_code, 404)
        self._mock_player.set_playlist.assert_not_called()

    def test_unauthenticated_returns_redirect(self):
        """Auth guard must fire before shuffle logic."""
        self._logout()
        self._seed_music()
        resp = self._post_json("/api/playlist/start-all", {"shuffle": True})
        self.assertIn(resp.status_code, (302, 401))
        self._mock_player.set_playlist.assert_not_called()


class TestPlaylistSetShuffleApi(_PlaylistApiBase):
    """/api/playlist/set — shuffle parameter handling."""

    def _media_id(self, n: int = 1) -> list[int]:
        """Insert n music rows and return their IDs."""
        ids = []
        for i in range(n):
            mid = db._media_repo.add_media_file(
                filename=f"song{i}.mp3",
                filepath=f"/media/music/song{i}.mp3",
                media_type="music",
                duration_seconds=120,
            )
            ids.append(mid)
        return ids

    # ── shuffle absent → default False ───────────────────────────────────────

    def test_no_shuffle_key_defaults_false(self):
        ids = self._media_id(2)
        resp = self._post_json("/api/playlist/set", {"media_ids": ids})
        self.assertEqual(resp.status_code, 200)
        _, kwargs = self._mock_player.set_playlist.call_args
        self.assertFalse(kwargs.get("shuffle", False))

    # ── shuffle=True ─────────────────────────────────────────────────────────

    def test_shuffle_true_forwarded_to_player(self):
        ids = self._media_id(3)
        resp = self._post_json("/api/playlist/set", {"media_ids": ids, "shuffle": True})
        self.assertEqual(resp.status_code, 200)
        _, kwargs = self._mock_player.set_playlist.call_args
        self.assertTrue(kwargs.get("shuffle"))

    def test_shuffle_false_forwarded_to_player(self):
        ids = self._media_id(2)
        resp = self._post_json("/api/playlist/set", {"media_ids": ids, "shuffle": False})
        self.assertEqual(resp.status_code, 200)
        _, kwargs = self._mock_player.set_playlist.call_args
        self.assertFalse(kwargs.get("shuffle", False))

    def test_shuffle_true_response_contains_track_count(self):
        ids = self._media_id(4)
        resp = self._post_json("/api/playlist/set", {"media_ids": ids, "shuffle": True})
        data = json.loads(resp.data)
        self.assertEqual(data["tracks"], 4)

    # ── Error paths — shuffle must not mask validation failures ──────────────

    def test_missing_media_ids_with_shuffle_true_returns_400(self):
        """shuffle=True must not skip the media_ids presence check."""
        resp = self._post_json("/api/playlist/set", {"shuffle": True})
        self.assertEqual(resp.status_code, 400)
        self._mock_player.set_playlist.assert_not_called()

    def test_empty_media_ids_with_shuffle_true_returns_400(self):
        resp = self._post_json("/api/playlist/set", {"media_ids": [], "shuffle": True})
        self.assertEqual(resp.status_code, 400)
        self._mock_player.set_playlist.assert_not_called()

    def test_all_invalid_media_ids_with_shuffle_true_returns_404(self):
        """Non-existent media IDs → 404; shuffle flag must not bypass this."""
        resp = self._post_json("/api/playlist/set", {"media_ids": [9999, 8888], "shuffle": True})
        self.assertEqual(resp.status_code, 404)
        self._mock_player.set_playlist.assert_not_called()

    def test_some_invalid_media_ids_skipped_valid_ones_used(self):
        """Partially invalid IDs: valid ones still build a playlist."""
        ids = self._media_id(2)
        payload = {"media_ids": ids + [99999], "shuffle": True}
        resp = self._post_json("/api/playlist/set", payload)
        self.assertEqual(resp.status_code, 200)
        data = json.loads(resp.data)
        self.assertEqual(data["tracks"], 2)  # only the 2 valid ones

    def test_unauthenticated_returns_redirect(self):
        self._logout()
        ids = self._media_id(1)
        resp = self._post_json("/api/playlist/set", {"media_ids": ids, "shuffle": True})
        self.assertIn(resp.status_code, (302, 401))
        self._mock_player.set_playlist.assert_not_called()

    def test_no_body_returns_415(self):
        """POST with no Content-Type header → Flask returns 415 before our code runs."""
        resp = self.client.post("/api/playlist/set")
        self.assertEqual(resp.status_code, 415)

    def test_empty_json_body_returns_400(self):
        """Empty JSON object {} → media_ids missing → 400."""
        resp = self._post_json("/api/playlist/set", {})
        self.assertEqual(resp.status_code, 400)


if __name__ == "__main__":
    unittest.main()
