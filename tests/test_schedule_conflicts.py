"""Schedule conflict validation tests."""
import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import database as db
import scheduler as scheduler_module
from database.media_repository import MediaRepository
from database.playback_repository import PlaybackRepository
from database.schedule_repository import ScheduleRepository
from scheduler import Scheduler
from services.schedule_conflict_service import (
    find_conflict_for_one_time,
    find_conflict_for_recurring,
    has_self_overlap_for_interval,
    resolve_duration_seconds,
)
from web_panel import app


class ScheduleConflictTestCase(unittest.TestCase):
    """Tests for schedule conflict service and route-level guards."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self._test_db_path = os.path.join(self._tmpdir.name, "test_schedule_conflicts.db")

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

    @staticmethod
    def _dt(year, month, day, hour, minute):
        return datetime(year, month, day, hour, minute)

    @staticmethod
    def _message_bytes():
        return "Seçtiğiniz süreyi kapsayan başka bir plan vardır.".encode("utf-8")

    def _add_media(self, filename: str, duration_seconds: int, media_type: str = "music") -> int:
        return db.add_media_file(
            filename=filename,
            filepath=f"/tmp/{filename}",
            media_type=media_type,
            duration_seconds=duration_seconds,
        )

    def test_one_time_conflict_and_boundary_touch(self):
        long_media = self._add_media("long.mp3", 420)
        candidate_media = self._add_media("candidate.mp3", 30)
        first_start = self._dt(2026, 2, 9, 10, 0)
        db.add_one_time_schedule(long_media, first_start)

        overlap = find_conflict_for_one_time(self._dt(2026, 2, 9, 10, 2), candidate_media)
        self.assertIsNotNone(overlap)
        self.assertEqual(overlap["type"], "one_time")

        no_overlap = find_conflict_for_one_time(self._dt(2026, 2, 9, 10, 7), candidate_media)
        self.assertIsNone(no_overlap)

    def test_one_time_conflicts_with_specific_recurring(self):
        recurring_media = self._add_media("recurring.mp3", 300)
        candidate_media = self._add_media("candidate.mp3", 30)
        day = self._dt(2026, 2, 9, 10, 0)
        db.add_recurring_schedule(
            media_id=recurring_media,
            days_of_week=[day.weekday()],
            start_time="10:00",
            specific_times=["10:00"],
        )

        conflict = find_conflict_for_one_time(self._dt(2026, 2, 9, 10, 3), candidate_media)
        self.assertIsNotNone(conflict)
        self.assertEqual(conflict["type"], "recurring")

    def test_interval_self_overlap_rule(self):
        self.assertTrue(has_self_overlap_for_interval(420, 2))
        self.assertFalse(has_self_overlap_for_interval(120, 2))

    def test_duration_fallback_for_unknown_duration(self):
        media_id = self._add_media("unknown_duration.mp3", 0)
        self.assertEqual(resolve_duration_seconds(media_id), 120)

    def test_overnight_conflict_is_detected(self):
        recurring_media = self._add_media("overnight.mp3", 420)
        candidate_media = self._add_media("candidate.mp3", 30)
        start_dt = self._dt(2026, 2, 9, 23, 58)
        db.add_recurring_schedule(
            media_id=recurring_media,
            days_of_week=[start_dt.weekday()],
            start_time="23:58",
            specific_times=["23:58"],
        )

        conflict_dt = start_dt + timedelta(minutes=4)
        conflict = find_conflict_for_one_time(conflict_dt, candidate_media)
        self.assertIsNotNone(conflict)
        self.assertEqual(conflict["type"], "recurring")

    def test_route_one_time_conflict_returns_flash_error(self):
        long_media = self._add_media("long.mp3", 420)
        candidate_media = self._add_media("candidate.mp3", 30)
        base_dt = (datetime.now() + timedelta(days=1)).replace(
            hour=10,
            minute=0,
            second=0,
            microsecond=0,
        )
        db.add_one_time_schedule(long_media, base_dt)

        response = self.client.post(
            "/api/schedules/one-time",
            data={
                "media_id": str(candidate_media),
                "date": base_dt.strftime("%Y-%m-%d"),
                "time": (base_dt + timedelta(minutes=2)).strftime("%H:%M"),
            },
            follow_redirects=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn(self._message_bytes(), response.data)

    def test_route_recurring_conflict_returns_flash_error(self):
        long_media = self._add_media("long.mp3", 420)
        candidate_media = self._add_media("candidate.mp3", 30)
        base_dt = (datetime.now() + timedelta(days=2)).replace(
            hour=10,
            minute=0,
            second=0,
            microsecond=0,
        )
        db.add_one_time_schedule(long_media, base_dt)

        response = self.client.post(
            "/api/schedules/recurring",
            data={
                "media_id": str(candidate_media),
                "days_of_week": json.dumps([base_dt.weekday()]),
                "schedule_type": "specific",
                "specific_times": "10:03",
            },
            follow_redirects=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn(self._message_bytes(), response.data)

    def test_route_toggle_recurring_conflict_blocks_activation(self):
        active_media = self._add_media("active.mp3", 420)
        candidate_media = self._add_media("candidate.mp3", 30)
        weekday = (datetime.now() + timedelta(days=1)).weekday()

        db.add_recurring_schedule(
            media_id=active_media,
            days_of_week=[weekday],
            start_time="10:00",
            specific_times=["10:00"],
        )
        candidate_id = db.add_recurring_schedule(
            media_id=candidate_media,
            days_of_week=[weekday],
            start_time="10:02",
            specific_times=["10:02"],
        )
        db.toggle_recurring_schedule(candidate_id, False)

        response = self.client.post(
            f"/api/schedules/recurring/{candidate_id}/toggle",
            follow_redirects=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn(self._message_bytes(), response.data)
        schedules = db.get_all_recurring_schedules()
        candidate = next((item for item in schedules if item["id"] == candidate_id), None)
        self.assertIsNotNone(candidate)
        self.assertFalse(bool(candidate["is_active"]))

    def test_service_recurring_conflicts_with_pending_one_time(self):
        long_media = self._add_media("long.mp3", 420)
        recurring_media = self._add_media("recurring.mp3", 30)
        base_dt = self._dt(2026, 2, 9, 10, 0)
        db.add_one_time_schedule(long_media, base_dt)

        candidate = {
            "media_id": recurring_media,
            "days_of_week": [base_dt.weekday()],
            "specific_times": ["10:03"],
            "schedule_type": "specific",
        }
        conflict = find_conflict_for_recurring(candidate)
        self.assertIsNotNone(conflict)
        self.assertEqual(conflict["type"], "one_time")


class SchedulerStreamRuntimeRulesTestCase(unittest.TestCase):
    """Faz 4 scheduler + stream policy interaction tests."""

    def setUp(self):
        self.scheduler = Scheduler(check_interval_seconds=1)

    @staticmethod
    def _player_mock():
        player = MagicMock()
        player._playlist = []
        player._playlist_index = -1
        player._playlist_loop = True
        player._playlist_active = False
        player.is_playing = False
        player.play.return_value = True
        player.stop.return_value = True
        player.apply_playlist_state.return_value = True
        return player

    def test_non_announcement_one_time_skipped_when_stream_active(self):
        stream_service = MagicMock()
        stream_service.status.return_value = {"active": True, "state": "live"}
        player = self._player_mock()

        with patch.object(
            scheduler_module,
            "get_stream_service",
            return_value=stream_service,
        ), patch.object(
            scheduler_module,
            "get_player",
            return_value=player,
        ), patch.object(
            scheduler_module,
            "is_within_working_hours",
            return_value=True,
        ), patch.object(
            scheduler_module,
            "resolve_silence_policy",
            return_value={"silence_active": False, "policy": "none", "reason_code": "test"},
        ), patch.object(
            scheduler_module.db,
            "update_one_time_schedule_status",
        ) as mock_update_status:
            self.scheduler._play_media(
                "/tmp/music.mp3",
                schedule_id=10,
                is_one_time=True,
                is_announcement=False,
            )

        mock_update_status.assert_called_once_with(10, "cancelled")
        player.play.assert_not_called()

    def test_announcement_pauses_stream_and_schedules_resume_worker(self):
        stream_service = MagicMock()
        stream_service.status.return_value = {"active": True, "state": "live"}
        player = self._player_mock()

        with patch.object(
            scheduler_module,
            "get_stream_service",
            return_value=stream_service,
        ), patch.object(
            scheduler_module,
            "get_player",
            return_value=player,
        ), patch.object(
            scheduler_module,
            "is_within_working_hours",
            return_value=True,
        ), patch.object(
            scheduler_module,
            "resolve_silence_policy",
            return_value={"silence_active": False, "policy": "none", "reason_code": "test"},
        ), patch.object(
            scheduler_module.db,
            "get_playlist_state",
            return_value={"playlist": [], "index": -1, "loop": True, "active": False},
        ), patch.object(
            self.scheduler,
            "_start_stream_resume_worker_after_announcement",
        ) as mock_resume_worker:
            self.scheduler._play_media(
                "/tmp/announcement.mp3",
                schedule_id=11,
                is_one_time=True,
                is_announcement=True,
            )

        stream_service.pause_for_announcement.assert_called_once()
        mock_resume_worker.assert_called_once()

    def test_announcement_resume_worker_runs_even_when_play_fails(self):
        stream_service = MagicMock()
        stream_service.status.return_value = {"active": True, "state": "live"}
        player = self._player_mock()
        player.play.return_value = False

        with patch.object(
            scheduler_module,
            "get_stream_service",
            return_value=stream_service,
        ), patch.object(
            scheduler_module,
            "get_player",
            return_value=player,
        ), patch.object(
            scheduler_module,
            "is_within_working_hours",
            return_value=True,
        ), patch.object(
            scheduler_module,
            "resolve_silence_policy",
            return_value={"silence_active": False, "policy": "none", "reason_code": "test"},
        ), patch.object(
            scheduler_module.db,
            "get_playlist_state",
            return_value={"playlist": [], "index": -1, "loop": True, "active": False},
        ), patch.object(
            self.scheduler,
            "_start_stream_resume_worker_after_announcement",
        ) as mock_resume_worker:
            self.scheduler._play_media(
                "/tmp/announcement.mp3",
                schedule_id=12,
                is_one_time=True,
                is_announcement=True,
            )

        stream_service.pause_for_announcement.assert_called_once()
        mock_resume_worker.assert_called_once()

    def test_silence_active_force_stops_stream(self):
        stream_service = MagicMock()
        stream_service.status.return_value = {"active": True, "state": "live"}
        stream_service.policy_sender_alive.return_value = False

        with patch.object(
            scheduler_module,
            "get_stream_service",
            return_value=stream_service,
        ):
            self.scheduler._apply_stream_runtime_policy({"silence_active": True})

        stream_service.force_stop_by_policy.assert_called_once()
        stream_service.resume_after_policy.assert_not_called()

    def test_silence_end_resumes_stream_only_when_sender_alive(self):
        stream_service = MagicMock()
        stream_service.status.return_value = {"active": False, "state": "stopped_by_policy"}
        stream_service.policy_sender_alive.return_value = True
        self.scheduler._last_stream_silence_active = True

        with patch.object(
            scheduler_module,
            "get_stream_service",
            return_value=stream_service,
        ):
            self.scheduler._apply_stream_runtime_policy({"silence_active": False})

        stream_service.resume_after_policy.assert_called_once()

    def test_silence_end_attempts_resume_when_policy_stopped(self):
        stream_service = MagicMock()
        stream_service.status.return_value = {"active": False, "state": "stopped_by_policy"}
        stream_service.policy_sender_alive.return_value = False
        self.scheduler._last_stream_silence_active = True

        with patch.object(
            scheduler_module,
            "get_stream_service",
            return_value=stream_service,
        ):
            self.scheduler._apply_stream_runtime_policy({"silence_active": False})

        stream_service.resume_after_policy.assert_called_once()

    def test_restart_bootstrap_attempts_resume_when_policy_stopped_and_silence_inactive(self):
        stream_service = MagicMock()
        stream_service.status.return_value = {"active": False, "state": "stopped_by_policy"}

        with patch.object(
            scheduler_module,
            "get_stream_service",
            return_value=stream_service,
        ):
            self.scheduler._apply_stream_runtime_policy({"silence_active": False})

        stream_service.resume_after_policy.assert_called_once()
        assert self.scheduler._stream_policy_bootstrapped is True

    def test_restart_bootstrap_does_not_retry_forever_when_state_still_stopped(self):
        stream_service = MagicMock()
        stream_service.status.return_value = {"active": False, "state": "stopped_by_policy"}

        with patch.object(
            scheduler_module,
            "get_stream_service",
            return_value=stream_service,
        ):
            self.scheduler._apply_stream_runtime_policy({"silence_active": False})
            self.scheduler._apply_stream_runtime_policy({"silence_active": False})

        stream_service.resume_after_policy.assert_called_once()

    def test_resume_worker_single_flight_guard(self):
        self.scheduler._stream_resume_worker_in_progress = True

        with patch.object(scheduler_module.threading, "Thread") as mock_thread:
            started = self.scheduler._start_stream_resume_worker_after_announcement()

        assert started is False
        mock_thread.assert_not_called()

    def test_resume_worker_force_stops_when_silence_active_during_announcement(self):
        """P2: silence activates while announcement plays → force-stop, not resume."""
        stream_service = MagicMock()
        stream_service.policy_sender_alive.return_value = True
        player = self._player_mock()
        player.is_playing = False  # announcement already finished

        silence_decision = {"silence_active": True, "policy": "prayer"}

        with patch.object(
            scheduler_module,
            "get_stream_service",
            return_value=stream_service,
        ), patch.object(
            scheduler_module,
            "get_player",
            return_value=player,
        ), patch.object(
            scheduler_module,
            "resolve_silence_policy",
            return_value=silence_decision,
        ):
            self.scheduler._resume_stream_after_announcement_worker()

        stream_service.force_stop_by_policy.assert_called_once()
        stream_service.resume_after_announcement.assert_not_called()


if __name__ == "__main__":
    unittest.main(verbosity=2)
