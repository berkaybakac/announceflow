"""Slot map service tests."""
import os
import tempfile
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

import database as db
from database.media_repository import MediaRepository
from database.playback_repository import PlaybackRepository
from database.schedule_repository import ScheduleRepository
from services.slot_map_service import get_day_slots, get_week_slots
from web_panel import app


class SlotMapServiceTestCase(unittest.TestCase):
    """Tests for slot map service."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self._test_db_path = os.path.join(self._tmpdir.name, "test_slot_map.db")

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
        with self.client.session_transaction() as sess:
            sess["logged_in"] = True

    def tearDown(self):
        db.DATABASE_PATH = self._old_db_path
        db._media_repo = self._old_media_repo
        db._schedule_repo = self._old_schedule_repo
        db._playback_repo = self._old_playback_repo
        self._tmpdir.cleanup()

    def _add_media(self, filename="test.mp3", media_type="announcement", duration=300):
        """Add a test media file and return its ID."""
        media_id = db.add_media_file(filename, f"/tmp/{filename}", media_type, duration)
        return media_id

    @patch("services.slot_map_service.load_config")
    def test_empty_day_returns_structure(self, mock_config):
        """Empty day returns valid structure with no slots."""
        mock_config.return_value = {
            "working_hours_enabled": False,
        }

        result = get_day_slots("2026-03-25")

        self.assertEqual(result["date"], "2026-03-25")
        self.assertIn("working_hours", result)
        self.assertIn("slots", result)
        self.assertEqual(result["slots"], [])
        self.assertEqual(result["day_of_week"], 2)  # Wednesday

    @patch("services.slot_map_service.load_config")
    def test_one_time_schedule_appears_in_slots(self, mock_config):
        """Pending one-time schedule shows as a slot."""
        mock_config.return_value = {
            "working_hours_enabled": False,
        }

        media_id = self._add_media(duration=300)  # 5 minutes
        # Schedule for a future date - use a Wednesday
        target_dt = datetime(2026, 3, 25, 14, 30)
        db.add_one_time_schedule(media_id, target_dt, "test")

        result = get_day_slots("2026-03-25")

        self.assertEqual(len(result["slots"]), 1)
        slot = result["slots"][0]
        self.assertEqual(slot["start"], "14:30")
        self.assertEqual(slot["end"], "14:35")
        self.assertEqual(slot["type"], "one_time")
        self.assertEqual(slot["source_type"], "one_time")
        self.assertIn("group_key", slot)

    @patch("services.slot_map_service.load_config")
    def test_recurring_schedule_appears_on_correct_day(self, mock_config):
        """Recurring schedule only appears on its configured days."""
        mock_config.return_value = {
            "working_hours_enabled": False,
        }

        media_id = self._add_media(duration=120)  # 2 minutes
        # Add recurring for Monday (0) and Wednesday (2)
        db.add_recurring_schedule(
            media_id, [0, 2], "10:00",
            specific_times=["10:00", "15:00"],
        )

        # Wednesday = weekday 2
        result = get_day_slots("2026-03-25")
        # Should have 2 slots (10:00 and 15:00)
        recurring_slots = [s for s in result["slots"] if s["type"] == "recurring"]
        self.assertEqual(len(recurring_slots), 2)
        self.assertEqual(recurring_slots[0]["start"], "10:00")
        self.assertEqual(recurring_slots[1]["start"], "15:00")

        # Thursday = weekday 3, not in schedule
        result = get_day_slots("2026-03-26")
        recurring_slots = [s for s in result["slots"] if s["type"] == "recurring"]
        self.assertEqual(len(recurring_slots), 0)

    @patch("services.slot_map_service.load_config")
    def test_working_hours_in_response(self, mock_config):
        """Working hours config is passed through."""
        mock_config.return_value = {
            "working_hours_enabled": True,
            "working_hours_start": "09:00",
            "working_hours_end": "18:00",
        }

        result = get_day_slots("2026-03-25")

        self.assertTrue(result["working_hours"]["enabled"])
        self.assertEqual(result["working_hours"]["start"], "09:00")
        self.assertEqual(result["working_hours"]["end"], "18:00")

    @patch("services.slot_map_service.load_config")
    def test_prayer_slots_appear(self, mock_config):
        """Prayer time windows appear when enabled and cached."""
        mock_config.return_value = {
            "working_hours_enabled": False,
            "prayer_times_enabled": True,
            "prayer_times_city": "İstanbul",
            "prayer_times_district": "Kadıköy",
        }

        fake_cache = {
            "İstanbul_Kadıköy_2026-03-25": {
                "imsak": "05:30",
                "gunes": "07:00",
                "ogle": "12:15",
                "ikindi": "15:30",
                "aksam": "18:10",
                "yatsi": "19:30",
                "date": "2026-03-25",
            }
        }

        with patch("prayer_times._load_cache", return_value=fake_cache):
            result = get_day_slots("2026-03-25")

        prayer_slots = [s for s in result["slots"] if s["type"] == "prayer"]
        self.assertEqual(len(prayer_slots), 6)
        # Check first prayer slot (imsak 05:30 → window 05:29-05:36)
        imsak_slot = prayer_slots[0]
        self.assertEqual(imsak_slot["start"], "05:29")
        self.assertEqual(imsak_slot["end"], "05:36")
        self.assertIn("Sessiz", imsak_slot["label"])
        self.assertIn("İmsak", imsak_slot["label"])
        self.assertIn("anons etkilenmez", imsak_slot["label"])

    @patch("services.slot_map_service.load_config")
    def test_week_slots_returns_seven_days(self, mock_config):
        """Week slots returns exactly 7 days."""
        mock_config.return_value = {
            "working_hours_enabled": False,
        }

        result = get_week_slots("2026-03-25")

        self.assertIn("days", result)
        self.assertEqual(len(result["days"]), 7)
        # First day should be Monday
        self.assertEqual(result["days"][0]["day_of_week"], 0)
        # Last day should be Sunday
        self.assertEqual(result["days"][6]["day_of_week"], 6)

    @patch("services.slot_map_service.load_config")
    def test_slots_sorted_by_start_time(self, mock_config):
        """Slots are returned sorted by start time."""
        mock_config.return_value = {
            "working_hours_enabled": False,
        }

        media_id = self._add_media(duration=120)
        # Add two one-time schedules out of order
        db.add_one_time_schedule(media_id, datetime(2026, 3, 25, 16, 0), "late")
        db.add_one_time_schedule(media_id, datetime(2026, 3, 25, 9, 0), "early")

        result = get_day_slots("2026-03-25")

        self.assertEqual(len(result["slots"]), 2)
        self.assertEqual(result["slots"][0]["start"], "09:00")
        self.assertEqual(result["slots"][1]["start"], "16:00")

    @patch("services.slot_map_service.load_config")
    def test_invalid_date_falls_back_to_today(self, mock_config):
        """Invalid date string falls back to today."""
        mock_config.return_value = {
            "working_hours_enabled": False,
        }

        result = get_day_slots("not-a-date")
        today = datetime.now().strftime("%Y-%m-%d")
        self.assertEqual(result["date"], today)

    @patch("services.slot_map_service.load_config")
    def test_api_day_slots_endpoint(self, mock_config):
        """GET /api/schedules/day-slots returns JSON."""
        mock_config.return_value = {
            "working_hours_enabled": False,
        }

        resp = self.client.get("/api/schedules/day-slots?date=2026-03-25")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(data["date"], "2026-03-25")
        self.assertIn("slots", data)

    @patch("services.slot_map_service.load_config")
    def test_api_week_slots_endpoint(self, mock_config):
        """GET /api/schedules/week-slots returns 7 days."""
        mock_config.return_value = {
            "working_hours_enabled": False,
        }

        resp = self.client.get("/api/schedules/week-slots?date=2026-03-25")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertIn("days", data)
        self.assertEqual(len(data["days"]), 7)


    # -- Error category --

    @patch("services.slot_map_service.load_config")
    def test_prayer_slots_graceful_when_import_fails(self, mock_config):
        """Prayer slots return empty when prayer module fails."""
        mock_config.return_value = {
            "working_hours_enabled": False,
            "prayer_times_enabled": True,
            "prayer_times_city": "İstanbul",
            "prayer_times_district": "Kadıköy",
        }

        with patch("services.slot_map_service._resolve_prayer_times", side_effect=ImportError("no module")):
            result = get_day_slots("2026-03-25")

        prayer_slots = [s for s in result["slots"] if s["type"] == "prayer"]
        self.assertEqual(len(prayer_slots), 0)

    @patch("services.slot_map_service.load_config")
    def test_corrupted_schedule_datetime_skipped(self, mock_config):
        """One-time schedule with unparseable datetime is skipped."""
        mock_config.return_value = {"working_hours_enabled": False}

        media_id = self._add_media(duration=120)
        db.add_one_time_schedule(media_id, datetime(2026, 3, 25, 10, 0), "good")

        # Manually corrupt a schedule datetime via direct DB access
        conn = db._schedule_repo.get_connection()
        conn.execute(
            "INSERT INTO one_time_schedules (media_id, scheduled_datetime, status) VALUES (?, ?, ?)",
            (media_id, "not-a-date", "pending"),
        )
        conn.commit()

        result = get_day_slots("2026-03-25")
        # Only the valid schedule should appear
        self.assertEqual(len(result["slots"]), 1)
        self.assertEqual(result["slots"][0]["start"], "10:00")

    # -- Edge category --

    @patch("services.slot_map_service.load_config")
    def test_overnight_slot_splits_at_midnight(self, mock_config):
        """A slot starting at 23:58 with 5min duration appears on both days."""
        mock_config.return_value = {"working_hours_enabled": False}

        media_id = self._add_media(duration=300)  # 5 minutes
        # 23:58 on Wednesday → should overflow into Thursday
        db.add_one_time_schedule(media_id, datetime(2026, 3, 25, 23, 58), "overnight")

        # Wednesday: slot from 23:58 to 23:59
        wed = get_day_slots("2026-03-25")
        wed_slots = [s for s in wed["slots"] if s["type"] == "one_time"]
        self.assertEqual(len(wed_slots), 1)
        self.assertEqual(wed_slots[0]["start"], "23:58")
        self.assertEqual(wed_slots[0]["end"], "23:59")

        # Thursday: overflow 0:00 to 0:03
        thu = get_day_slots("2026-03-26")
        thu_slots = [s for s in thu["slots"] if s["type"] == "one_time"]
        self.assertEqual(len(thu_slots), 1)
        self.assertEqual(thu_slots[0]["start"], "00:00")
        self.assertEqual(thu_slots[0]["end"], "00:03")

    @patch("services.slot_map_service.load_config")
    def test_overnight_recurring_wraps_sunday_to_monday(self, mock_config):
        """Recurring schedule at 23:55 on Sunday (10min) overflows into Monday."""
        mock_config.return_value = {"working_hours_enabled": False}

        media_id = self._add_media(duration=600)  # 10 minutes
        # Sunday = weekday 6
        db.add_recurring_schedule(
            media_id, [6], "23:55",
            specific_times=["23:55"],
        )

        # Sunday: slot 23:55 → 23:59
        sun = get_day_slots("2026-03-29")  # Sunday
        recurring = [s for s in sun["slots"] if s["type"] == "recurring"]
        self.assertEqual(len(recurring), 1)
        self.assertEqual(recurring[0]["start"], "23:55")
        self.assertEqual(recurring[0]["end"], "23:59")

        # Monday: overflow 00:00 → 00:05
        mon = get_day_slots("2026-03-30")  # Monday
        recurring = [s for s in mon["slots"] if s["type"] == "recurring"]
        self.assertEqual(len(recurring), 1)
        self.assertEqual(recurring[0]["start"], "00:00")
        self.assertEqual(recurring[0]["end"], "00:05")

    @patch("services.slot_map_service.load_config")
    def test_slot_ending_exactly_at_midnight_no_overflow(self, mock_config):
        """A slot ending exactly at 24:00 (1440 min) should not overflow."""
        mock_config.return_value = {"working_hours_enabled": False}

        media_id = self._add_media(duration=120)  # 2 minutes
        # 23:58 + 2min = 24:00 exactly → no overflow
        db.add_one_time_schedule(media_id, datetime(2026, 3, 25, 23, 58), "exact")

        wed = get_day_slots("2026-03-25")
        wed_slots = [s for s in wed["slots"] if s["type"] == "one_time"]
        self.assertEqual(len(wed_slots), 1)
        self.assertEqual(wed_slots[0]["start"], "23:58")

        # Thursday should have NO overflow from this slot
        thu = get_day_slots("2026-03-26")
        thu_slots = [s for s in thu["slots"] if s["type"] == "one_time"]
        self.assertEqual(len(thu_slots), 0)

    # -- Security category --

    @patch("services.slot_map_service.load_config")
    def test_api_day_slots_requires_login(self, mock_config):
        """day-slots endpoint requires authentication."""
        mock_config.return_value = {"working_hours_enabled": False}

        # Create a fresh client without logged_in session
        with app.test_client() as anon_client:
            resp = anon_client.get("/api/schedules/day-slots?date=2026-03-25")
            # Should redirect to login (302)
            self.assertEqual(resp.status_code, 302)
            self.assertIn("/login", resp.headers.get("Location", ""))

    @patch("services.slot_map_service.load_config")
    def test_api_week_slots_requires_login(self, mock_config):
        """week-slots endpoint requires authentication."""
        mock_config.return_value = {"working_hours_enabled": False}

        with app.test_client() as anon_client:
            resp = anon_client.get("/api/schedules/week-slots?date=2026-03-25")
            self.assertEqual(resp.status_code, 302)
            self.assertIn("/login", resp.headers.get("Location", ""))


if __name__ == "__main__":
    unittest.main()
