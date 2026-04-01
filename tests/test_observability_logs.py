"""Tests for observability log additions: system_health, playlist summaries,
daily_usage_summary, xrun_snapshot, and jitter_anomaly."""
import time
from unittest.mock import MagicMock, patch

from scheduler import Scheduler
from player import AudioPlayer
from _stream_receiver import (
    _log_xrun_snapshot,
    _log_jitter_anomaly,
    _XRUN_SNAPSHOT_INTERVAL,
    _JITTER_ANOMALY_INTERVAL,
)


# ---------------------------------------------------------------------------
# system_health throttle
# ---------------------------------------------------------------------------

class TestSystemHealthLog:
    def _make_scheduler(self) -> Scheduler:
        return Scheduler(check_interval_seconds=60)

    def test_first_call_emits_log(self, monkeypatch):
        """First _log_system_health call should emit a log_system event."""
        sched = self._make_scheduler()
        calls = []
        monkeypatch.setattr("scheduler.log_system", lambda event, data: calls.append((event, data)))
        monkeypatch.setattr("scheduler.get_player", lambda: MagicMock(get_state=lambda: {}))
        monkeypatch.setattr("scheduler.get_stream_service", lambda: MagicMock(status=lambda: {}))

        sched._log_system_health()
        assert len(calls) == 1
        assert calls[0][0] == "system_health"

    def test_throttle_prevents_rapid_calls(self, monkeypatch):
        """Calls within the interval window should be suppressed."""
        sched = self._make_scheduler()
        calls = []
        monkeypatch.setattr("scheduler.log_system", lambda event, data: calls.append((event, data)))
        monkeypatch.setattr("scheduler.get_player", lambda: MagicMock(get_state=lambda: {}))
        monkeypatch.setattr("scheduler.get_stream_service", lambda: MagicMock(status=lambda: {}))

        sched._log_system_health()
        sched._log_system_health()
        sched._log_system_health()

        assert len(calls) == 1, "Throttle should suppress rapid successive calls"

    def test_emits_after_interval_expires(self, monkeypatch):
        """After the interval elapses, a new log should be emitted."""
        sched = self._make_scheduler()
        sched._system_health_log_interval_seconds = 0  # no throttle
        calls = []
        monkeypatch.setattr("scheduler.log_system", lambda event, data: calls.append((event, data)))
        monkeypatch.setattr("scheduler.get_player", lambda: MagicMock(get_state=lambda: {}))
        monkeypatch.setattr("scheduler.get_stream_service", lambda: MagicMock(status=lambda: {}))

        sched._log_system_health()
        sched._log_system_health()

        assert len(calls) == 2

    def test_fallback_values_when_proc_unavailable(self, monkeypatch):
        """On non-Linux (no /proc), health data should use -1 fallbacks."""
        sched = self._make_scheduler()
        calls = []
        monkeypatch.setattr("scheduler.log_system", lambda event, data: calls.append((event, data)))
        monkeypatch.setattr("scheduler.get_player", lambda: MagicMock(get_state=lambda: {}))
        monkeypatch.setattr("scheduler.get_stream_service", lambda: MagicMock(status=lambda: {}))

        # builtins.open will fail for /proc/* on macOS — fallback should kick in
        sched._log_system_health()

        data = calls[0][1]
        # On macOS: mem and load come from /proc, should be -1
        # disk_used_pct should be valid (shutil.disk_usage works everywhere)
        assert data["disk_used_pct"] > 0
        assert isinstance(data["mem_total_mb"], (int, float))
        assert isinstance(data["load_1m"], (int, float))


# ---------------------------------------------------------------------------
# playlist_session_summary
# ---------------------------------------------------------------------------

class TestPlaylistSessionSummary:
    def _make_player(self) -> AudioPlayer:
        with patch("player.AudioPlayer._build_alsa_device_candidates", return_value=[]), \
             patch("player.AudioPlayer._build_alsa_card_candidates", return_value=[]):
            return AudioPlayer()

    def test_no_log_when_no_tracks(self, monkeypatch):
        """If no tracks were played or skipped, summary should not be logged."""
        player = self._make_player()
        calls = []
        monkeypatch.setattr("player.log_system", lambda event, data: calls.append((event, data)))

        player.log_session_summary()
        assert len(calls) == 0, "Should not log when no tracks played"

    def test_logs_after_tracks_played(self, monkeypatch):
        """After tracks are played, summary should contain correct counts."""
        player = self._make_player()
        calls = []
        monkeypatch.setattr("player.log_system", lambda event, data: calls.append((event, data)))

        player._session_tracks_played = 5
        player._session_tracks_skipped = 2
        player._session_play_seconds = 300.0

        player.log_session_summary()

        assert len(calls) == 1
        event, data = calls[0]
        assert event == "playlist_session_summary"
        assert data["tracks_played"] == 5
        assert data["tracks_skipped"] == 2
        assert data["play_seconds"] == 300.0
        assert data["play_minutes"] == 5.0

    def test_counters_reset_after_summary(self, monkeypatch):
        """Counters should be zeroed after logging the summary."""
        player = self._make_player()
        monkeypatch.setattr("player.log_system", lambda event, data: None)

        player._session_tracks_played = 3
        player._session_tracks_skipped = 1
        player._session_play_seconds = 120.0

        player.log_session_summary()

        assert player._session_tracks_played == 0
        assert player._session_tracks_skipped == 0
        assert player._session_play_seconds == 0.0

    def test_accounts_for_in_progress_track(self, monkeypatch):
        """If a track is currently playing, its elapsed time should be included."""
        player = self._make_player()
        calls = []
        monkeypatch.setattr("player.log_system", lambda event, data: calls.append((event, data)))

        player._session_tracks_played = 1
        player._session_play_seconds = 100.0
        player._session_play_started_at = time.monotonic() - 50  # 50s ago

        player.log_session_summary()

        data = calls[0][1]
        # Should be ~150s (100 accumulated + 50 in-progress)
        assert data["play_seconds"] >= 149.0
        assert data["play_seconds"] <= 152.0

    def test_daily_playlist_summary_snapshot_and_reset(self):
        player = self._make_player()
        player._daily_tracks_played = 4
        player._daily_tracks_skipped = 1
        player._daily_play_seconds = 180.0

        snap = player.get_daily_playlist_summary(reset=False)
        assert snap["tracks_played"] == 4
        assert snap["tracks_skipped"] == 1
        assert snap["play_seconds"] == 180.0
        assert snap["play_minutes"] == 3.0
        assert snap["play_hours"] == 0.05

        reset_snap = player.get_daily_playlist_summary(reset=True)
        assert reset_snap["tracks_played"] == 4
        assert reset_snap["tracks_skipped"] == 1
        assert reset_snap["play_seconds"] == 180.0

        after = player.get_daily_playlist_summary(reset=False)
        assert after["tracks_played"] == 0
        assert after["tracks_skipped"] == 0
        assert after["play_seconds"] == 0.0


# ---------------------------------------------------------------------------
# playlist_loop_restart
# ---------------------------------------------------------------------------

class TestPlaylistLoopRestart:
    def _make_player(self) -> AudioPlayer:
        with patch("player.AudioPlayer._build_alsa_device_candidates", return_value=[]), \
             patch("player.AudioPlayer._build_alsa_card_candidates", return_value=[]):
            return AudioPlayer()

    def test_loop_restart_emits_log(self, monkeypatch):
        """When playlist wraps around with loop=True, a log should be emitted."""
        player = self._make_player()
        calls = []
        monkeypatch.setattr("player.log_play", lambda event, data: calls.append((event, data)))
        # Stub play() to succeed so play_next finishes
        monkeypatch.setattr(player, "play", lambda *a, **kw: True)

        player._playlist = ["/tmp/a.mp3", "/tmp/b.mp3"]
        player._playlist_active = True
        player._playlist_loop = True
        player._playlist_index = 1  # at last track

        player.play_next()

        loop_events = [(e, d) for e, d in calls if e == "playlist_loop_restart"]
        assert len(loop_events) == 1
        assert loop_events[0][1]["total_tracks"] == 2

    def test_no_loop_restart_log_when_loop_disabled(self, monkeypatch):
        """When loop=False, no loop_restart log should appear (playlist_end instead)."""
        player = self._make_player()
        calls = []
        monkeypatch.setattr("player.log_play", lambda event, data: calls.append((event, data)))

        player._playlist = ["/tmp/a.mp3"]
        player._playlist_active = True
        player._playlist_loop = False
        player._playlist_index = 0  # at last track

        player.play_next()

        loop_events = [e for e, d in calls if e == "playlist_loop_restart"]
        assert len(loop_events) == 0


# ---------------------------------------------------------------------------
# daily_usage_summary
# ---------------------------------------------------------------------------

class TestDailyUsageSummary:
    def _make_scheduler(self) -> Scheduler:
        return Scheduler(check_interval_seconds=60)

    def test_no_log_on_same_day(self, monkeypatch):
        """No summary emitted while the date stays the same."""
        sched = self._make_scheduler()
        calls = []
        monkeypatch.setattr("scheduler.log_system", lambda event, data: calls.append((event, data)))

        sched._check_daily_usage_summary()  # sets _daily_current_date
        sched._check_daily_usage_summary()  # same day, should not log

        assert len(calls) == 0

    def test_emits_on_date_change(self, monkeypatch):
        """Summary is emitted when the date rolls over."""
        sched = self._make_scheduler()
        calls = []
        monkeypatch.setattr("scheduler.log_system", lambda event, data: calls.append((event, data)))
        fake_player = MagicMock()
        fake_player.get_daily_playlist_summary.return_value = {
            "tracks_played": 8,
            "tracks_skipped": 2,
            "play_seconds": 600.0,
            "play_minutes": 10.0,
            "play_hours": 0.167,
        }
        monkeypatch.setattr("scheduler.get_player", lambda: fake_player)

        sched._daily_current_date = "2026-03-30"
        sched._daily_triggers_one_time = 3
        sched._daily_triggers_recurring = 7
        sched._daily_prayer_silences = 5
        sched._daily_working_hours_blocks = 1

        # Simulate date change
        monkeypatch.setattr("scheduler.datetime", type("FakeDT", (), {
            "now": staticmethod(lambda: type("D", (), {"strftime": lambda self, f: "2026-03-31"})()),
        }))

        sched._check_daily_usage_summary()

        assert len(calls) == 2
        daily = [entry for entry in calls if entry[0] == "daily_usage_summary"][0][1]
        playlist_daily = [
            entry for entry in calls if entry[0] == "playlist_daily_summary"
        ][0][1]
        assert daily["date"] == "2026-03-30"
        assert daily["triggers_one_time"] == 3
        assert daily["triggers_recurring"] == 7
        assert daily["prayer_silences"] == 5
        assert daily["working_hours_blocks"] == 1
        assert playlist_daily["date"] == "2026-03-30"
        assert playlist_daily["tracks_played"] == 8
        assert playlist_daily["tracks_skipped"] == 2
        assert playlist_daily["play_seconds"] == 600.0

    def test_counters_reset_after_emit(self, monkeypatch):
        """Counters should be zeroed after the summary is emitted."""
        sched = self._make_scheduler()
        monkeypatch.setattr("scheduler.log_system", lambda event, data: None)
        fake_player = MagicMock()
        fake_player.get_daily_playlist_summary.return_value = {
            "tracks_played": 0,
            "tracks_skipped": 0,
            "play_seconds": 0.0,
            "play_minutes": 0.0,
            "play_hours": 0.0,
        }
        monkeypatch.setattr("scheduler.get_player", lambda: fake_player)

        sched._daily_current_date = "2026-03-30"
        sched._daily_triggers_one_time = 5
        sched._daily_triggers_recurring = 2
        sched._daily_prayer_silences = 3

        monkeypatch.setattr("scheduler.datetime", type("FakeDT", (), {
            "now": staticmethod(lambda: type("D", (), {"strftime": lambda self, f: "2026-03-31"})()),
        }))

        sched._check_daily_usage_summary()

        assert sched._daily_triggers_one_time == 0
        assert sched._daily_triggers_recurring == 0
        assert sched._daily_prayer_silences == 0
        assert sched._daily_working_hours_blocks == 0
        assert sched._daily_current_date == "2026-03-31"


# ---------------------------------------------------------------------------
# xrun_snapshot throttle
# ---------------------------------------------------------------------------

class TestXrunSnapshot:
    def test_first_xrun_emits_snapshot(self, monkeypatch):
        """First xrun should emit an xrun_snapshot log."""
        import _stream_receiver as mod
        calls = []
        monkeypatch.setattr(mod, "_safe_log_error", lambda event, data: calls.append((event, data)))
        monkeypatch.setattr(mod, "_last_xrun_snapshot_mono", 0.0)

        counters = {"alsa_xrun": 1, "udp_overrun": 0}
        _log_xrun_snapshot(counters, "test-cid")

        assert len(calls) == 1
        assert calls[0][0] == "xrun_snapshot"
        assert calls[0][1]["correlation_id"] == "test-cid"
        assert calls[0][1]["xrun_count_so_far"] == 1

    def test_throttle_suppresses_rapid_snapshots(self, monkeypatch):
        """Rapid xruns should only produce one snapshot within the interval."""
        import _stream_receiver as mod
        calls = []
        monkeypatch.setattr(mod, "_safe_log_error", lambda event, data: calls.append((event, data)))
        # Set last snapshot to "just now"
        monkeypatch.setattr(mod, "_last_xrun_snapshot_mono", time.monotonic())

        counters = {"alsa_xrun": 5, "udp_overrun": 0}
        _log_xrun_snapshot(counters, "test-cid")

        assert len(calls) == 0, "Should be throttled"


# ---------------------------------------------------------------------------
# jitter_anomaly throttle
# ---------------------------------------------------------------------------

class TestJitterAnomaly:
    def test_first_overrun_emits_anomaly(self, monkeypatch):
        """First UDP overrun should emit a jitter_anomaly log."""
        import _stream_receiver as mod
        calls = []
        monkeypatch.setattr(mod, "_safe_log_error", lambda event, data: calls.append((event, data)))
        monkeypatch.setattr(mod, "_last_jitter_anomaly_mono", 0.0)

        counters = {"alsa_xrun": 0, "udp_overrun": 1}
        _log_jitter_anomaly(counters, "test-cid", "udp_overrun")

        assert len(calls) == 1
        assert calls[0][0] == "stream_jitter_anomaly"
        assert calls[0][1]["trigger"] == "udp_overrun"

    def test_throttle_suppresses_rapid_anomalies(self, monkeypatch):
        """Rapid anomalies should be throttled."""
        import _stream_receiver as mod
        calls = []
        monkeypatch.setattr(mod, "_safe_log_error", lambda event, data: calls.append((event, data)))
        monkeypatch.setattr(mod, "_last_jitter_anomaly_mono", time.monotonic())

        counters = {"alsa_xrun": 0, "udp_overrun": 3}
        _log_jitter_anomaly(counters, "test-cid", "udp_overrun")

        assert len(calls) == 0, "Should be throttled"


# ---------------------------------------------------------------------------
# web_event_count in logger
# ---------------------------------------------------------------------------

class TestWebEventCount:
    def test_log_web_increments_counter(self, monkeypatch):
        """Each log_web call should increment the global counter."""
        import logger as mod
        mod._web_event_count = 0
        monkeypatch.setattr(mod, "_event_logger", MagicMock())

        mod.log_web("login", {"username": "admin"})
        mod.log_web("upload", {"filename": "test.mp3"})
        mod.log_web("stop")

        assert mod._web_event_count == 3

    def test_get_and_reset_returns_and_zeroes(self, monkeypatch):
        """get_and_reset_web_event_count should return count and reset to 0."""
        import logger as mod
        mod._web_event_count = 42

        result = mod.get_and_reset_web_event_count()

        assert result == 42
        assert mod._web_event_count == 0

    def test_daily_summary_includes_web_events(self, monkeypatch):
        """daily_usage_summary should include web_events field."""
        sched = Scheduler(check_interval_seconds=60)
        calls = []
        monkeypatch.setattr("scheduler.log_system", lambda event, data: calls.append((event, data)))
        monkeypatch.setattr("scheduler.get_and_reset_web_event_count", lambda: 15)
        fake_player = MagicMock()
        fake_player.get_daily_playlist_summary.return_value = {
            "tracks_played": 1,
            "tracks_skipped": 0,
            "play_seconds": 30.0,
            "play_minutes": 0.5,
            "play_hours": 0.008,
        }
        monkeypatch.setattr("scheduler.get_player", lambda: fake_player)

        sched._daily_current_date = "2026-03-30"

        monkeypatch.setattr("scheduler.datetime", type("FakeDT", (), {
            "now": staticmethod(lambda: type("D", (), {"strftime": lambda self, f: "2026-03-31"})()),
        }))

        sched._check_daily_usage_summary()

        assert len(calls) == 2
        daily = [entry for entry in calls if entry[0] == "daily_usage_summary"][0][1]
        playlist_daily = [
            entry for entry in calls if entry[0] == "playlist_daily_summary"
        ][0][1]
        assert daily["web_events"] == 15
        assert playlist_daily["tracks_played"] == 1
