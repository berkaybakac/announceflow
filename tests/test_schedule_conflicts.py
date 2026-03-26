"""Schedule conflict validation tests."""
import json
import os
import tempfile
import time
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

    def test_route_one_time_announcement_conflict_is_soft_accepted(self):
        long_media = self._add_media("long_announcement.mp3", 420, media_type="announcement")
        candidate_media = self._add_media("candidate_announcement.mp3", 30, media_type="announcement")
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
        self.assertIn("Plan eklendi".encode("utf-8"), response.data)
        pending = db.get_pending_one_time_schedules()
        self.assertEqual(len(pending), 2)

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

    def test_route_recurring_announcement_conflict_is_soft_accepted(self):
        long_media = self._add_media("long_announcement.mp3", 420, media_type="announcement")
        candidate_media = self._add_media("candidate_announcement.mp3", 30, media_type="announcement")
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
        self.assertIn("Tekrarlı plan oluşturuldu".encode("utf-8"), response.data)
        all_recurring = db.get_all_recurring_schedules()
        self.assertEqual(len(all_recurring), 1)

    def test_route_recurring_announcement_interval_self_overlap_allowed(self):
        overlap_media = self._add_media("overlap_announcement.mp3", 420, media_type="announcement")
        weekday = (datetime.now() + timedelta(days=1)).weekday()

        response = self.client.post(
            "/api/schedules/recurring",
            data={
                "media_id": str(overlap_media),
                "days_of_week": json.dumps([weekday]),
                "schedule_type": "interval",
                "start_time": "09:00",
                "end_time": "09:10",
                "interval_minutes": "2",
            },
            follow_redirects=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn("Tekrarlı plan oluşturuldu".encode("utf-8"), response.data)
        all_recurring = db.get_all_recurring_schedules()
        self.assertEqual(len(all_recurring), 1)

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

    def test_scheduler_queue_lite_dedupes_same_minute(self):
        scheduler = Scheduler(check_interval_seconds=1)
        due_dt = self._dt(2026, 2, 9, 10, 0)

        first = scheduler._queue_announcement(
            filepath="/tmp/a.mp3",
            schedule_id=99,
            is_one_time=False,
            due_dt=due_dt,
            source="recurring",
            duration_seconds=15,
        )
        second = scheduler._queue_announcement(
            filepath="/tmp/a.mp3",
            schedule_id=99,
            is_one_time=False,
            due_dt=due_dt,
            source="recurring",
            duration_seconds=15,
        )

        self.assertTrue(first)
        self.assertFalse(second)
        self.assertEqual(len(scheduler._announcement_queue), 1)

    def test_scheduler_queue_enqueue_sets_one_time_status_to_queued(self):
        scheduler = Scheduler(check_interval_seconds=1)
        media_id = self._add_media("queued_announcement.mp3", 10, media_type="announcement")
        due_dt = datetime.now() + timedelta(minutes=5)
        schedule_id = db.add_one_time_schedule(media_id, due_dt)

        self.assertEqual(db.get_one_time_schedule(schedule_id)["status"], "pending")

        queued = scheduler._queue_announcement(
            filepath="/tmp/queued_announcement.mp3",
            schedule_id=schedule_id,
            is_one_time=True,
            due_dt=due_dt,
            source="one_time",
            duration_seconds=10,
        )

        self.assertTrue(queued)
        schedule = db.get_one_time_schedule(schedule_id)
        self.assertEqual(schedule["status"], "queued")

    def test_scheduler_queue_lite_drops_stale_and_cancels_one_time(self):
        scheduler = Scheduler(check_interval_seconds=1)
        scheduler._announcement_max_delay_seconds = 60

        media_id = self._add_media("stale_announcement.mp3", 10, media_type="announcement")
        stale_dt = datetime.now() - timedelta(minutes=10)
        schedule_id = db.add_one_time_schedule(media_id, stale_dt)

        queued = scheduler._queue_announcement(
            filepath="/tmp/stale_announcement.mp3",
            schedule_id=schedule_id,
            is_one_time=True,
            due_dt=stale_dt,
            source="one_time",
            duration_seconds=10,
        )
        self.assertTrue(queued)

        scheduler._drop_stale_announcement_queue_items()
        self.assertEqual(len(scheduler._announcement_queue), 0)

        schedule = next(
            (s for s in db.get_all_one_time_schedules() if s["id"] == schedule_id), None
        )
        self.assertIsNotNone(schedule)
        self.assertEqual(schedule["status"], "cancelled")

    def test_scheduler_queue_lite_soak_cleanup_no_tracking_leak(self):
        scheduler = Scheduler(check_interval_seconds=1)
        due_dt = self._dt(2026, 2, 9, 10, 0)

        for idx in range(10_000):
            queued = scheduler._queue_announcement(
                filepath="/tmp/soak.mp3",
                schedule_id=idx + 1,
                is_one_time=True,
                due_dt=due_dt,
                source="one_time",
                duration_seconds=5,
            )
            self.assertTrue(queued)
            item = scheduler._announcement_queue.popleft()
            scheduler._cleanup_queue_item_tracking(
                item, remove_dedupe=True, cancel_one_time=False
            )

        self.assertEqual(len(scheduler._announcement_queue), 0)
        self.assertEqual(len(scheduler._queued_one_time_ids), 0)
        self.assertEqual(len(scheduler._announcement_enqueued_keys), 0)

    def test_scheduler_queue_lite_drops_invalid_cancelled_one_time_before_dispatch(self):
        scheduler = Scheduler(check_interval_seconds=1)
        media_id = self._add_media("cancelled_before_dispatch.mp3", 10, media_type="announcement")
        due_dt = datetime.now() - timedelta(seconds=30)
        schedule_id = db.add_one_time_schedule(media_id, due_dt)

        queued = scheduler._queue_announcement(
            filepath="/tmp/cancelled_before_dispatch.mp3",
            schedule_id=schedule_id,
            is_one_time=True,
            due_dt=due_dt,
            source="one_time",
            duration_seconds=10,
        )
        self.assertTrue(queued)
        db.update_one_time_schedule_status(schedule_id, "cancelled")

        with patch.object(scheduler, "_play_media") as mock_play_media:
            scheduler._process_announcement_queue(
                outside_working_hours=False, silence_blocked=False
            )

        mock_play_media.assert_not_called()
        self.assertEqual(len(scheduler._announcement_queue), 0)
        self.assertNotIn(schedule_id, scheduler._queued_one_time_ids)
        self.assertEqual(scheduler._announcement_queue_counters["dropped_invalid"], 1)

    def test_scheduler_media_type_normalization_is_fail_safe_to_music(self):
        scheduler = Scheduler(check_interval_seconds=1)
        self.assertEqual(scheduler._normalize_media_type("announcement"), "announcement")
        self.assertEqual(scheduler._normalize_media_type(" Announcement "), "announcement")
        self.assertEqual(scheduler._normalize_media_type(None), "music")
        self.assertEqual(scheduler._normalize_media_type(""), "music")
        self.assertEqual(scheduler._normalize_media_type("legacy_unknown"), "music")

    def test_scheduler_queue_lite_stuck_watchdog_resets_current_item(self):
        scheduler = Scheduler(check_interval_seconds=1)
        media_id = self._add_media("stuck_item.mp3", 10, media_type="announcement")
        due_dt = datetime.now() - timedelta(seconds=30)
        schedule_id = db.add_one_time_schedule(media_id, due_dt)

        queued = scheduler._queue_announcement(
            filepath="/tmp/stuck_item.mp3",
            schedule_id=schedule_id,
            is_one_time=True,
            due_dt=due_dt,
            source="one_time",
            duration_seconds=5,
        )
        self.assertTrue(queued)

        item = scheduler._announcement_queue.popleft()
        scheduler._announcement_enqueued_keys.discard(item.get("dedupe_key"))
        item["started_ts"] = time.time() - 500
        item["expected_duration_seconds"] = 5
        scheduler._announcement_current = item

        player = MagicMock()
        player.is_playing = True
        player.stop.return_value = True
        with patch.object(scheduler_module, "get_player", return_value=player):
            scheduler._reset_stuck_current_announcement_if_needed()

        player.stop.assert_called_once()
        self.assertIsNone(scheduler._announcement_current)
        self.assertNotIn(schedule_id, scheduler._queued_one_time_ids)
        self.assertEqual(scheduler._announcement_queue_counters["stuck_reset"], 1)
        schedule = db.get_one_time_schedule(schedule_id)
        self.assertIsNotNone(schedule)
        self.assertEqual(schedule["status"], "cancelled")


    def test_scheduler_tick_guard_blocks_second_play_media_in_same_tick(self):
        scheduler = Scheduler(check_interval_seconds=1)

        player = MagicMock()
        player.is_playing = False
        player.play.return_value = True
        player.set_volume.return_value = True
        player._playback_session = "session_1"

        stream_service = MagicMock()
        stream_service.status.return_value = {"active": False}

        config = {"working_hours_enabled": False}

        with (
            patch.object(scheduler_module, "get_player", return_value=player),
            patch.object(scheduler_module, "get_stream_service", return_value=stream_service),
            patch.object(scheduler_module, "resolve_silence_policy", return_value={"silence_active": False}),
            patch.object(scheduler_module, "is_within_working_hours", return_value=True),
            patch.object(scheduler, "_get_cached_config", return_value=config),
            patch.object(scheduler, "_capture_restore_snapshot", return_value={"playlist_was_active": False, "playlist_files": [], "playlist_index": 0, "playlist_loop": False}),
            patch.object(scheduler, "_interrupt_for_scheduled_media"),
            patch.object(scheduler, "_start_scheduled_media", return_value=True),
            patch.object(scheduler, "_queue_restore_target", return_value=False),
        ):
            # First call should succeed and set the tick guard.
            result1 = scheduler._play_media("/tmp/music.mp3", schedule_id=1, is_one_time=True, is_announcement=False)
            self.assertTrue(result1)
            self.assertTrue(scheduler._tick_media_dispatched)

            # Second call in the same tick should be blocked.
            result2 = scheduler._play_media("/tmp/announce.mp3", schedule_id=2, is_one_time=False, is_announcement=True)
            self.assertFalse(result2)

            # Simulating a new tick resets the guard.
            scheduler._tick_media_dispatched = False
            result3 = scheduler._play_media("/tmp/announce2.mp3", schedule_id=3, is_one_time=False, is_announcement=True)
            self.assertTrue(result3)


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
