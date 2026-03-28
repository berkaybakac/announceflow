"""Announcement queue backlog and prayer-time silence tests.

İki üretim senaryosunu doğrular:

1. Ezan vakti sessizliği (prayer silence):
   - Ezan sırasında kuyruğa giren anons oynatılmaz (bloklanır).
   - Ezan bitince aynı anons kuyrukta kalmaya devam eder ve oynatılır.
   - Anons iptal edilmez — sadece bekler.

2. Mesai dışı birikim (outside-working-hours backlog):
   - Mesai dışında _check_*_schedules anons kuyruğa ALMAZ (fix sonrası).
   - One-time anons: DB'de "cancelled" olarak işaretlenir.
   - Recurring anons: sessizce atlanır.
   - Ezan (silence_blocked=True) bu fix'ten etkilenmez — ezan kuyruğu çalışmaya devam eder.
"""
from __future__ import annotations

import time
from collections import deque
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock

import pytest

import scheduler as scheduler_module
from scheduler import Scheduler


# ─── yardımcı fonksiyonlar ─────────────────────────────────────────────────


def _make_scheduler() -> Scheduler:
    s = Scheduler(check_interval_seconds=60)
    s._announcement_gap_seconds = 0        # testlerde gap olmasın
    s._announcement_next_allowed_monotonic = 0.0  # gap bloku yok
    return s


def _fake_player(*, is_playing: bool = False) -> MagicMock:
    p = MagicMock()
    p.is_playing = is_playing
    p._playback_session = None
    p._playlist_active = False
    return p


def _inject_item(
    sched: Scheduler,
    *,
    schedule_id: int,
    is_one_time: bool,
    age_seconds: int = 0,
) -> None:
    """Kuyruğa direkt item ekle — DB çağrısı yapmaz."""
    now_ts = time.time()
    due_ts = now_ts - age_seconds
    key = f"test:{schedule_id}:{schedule_id}"
    item = {
        "dedupe_key": key,
        "filepath": f"/tmp/anons_{schedule_id}.mp3",
        "schedule_id": schedule_id,
        "is_one_time": is_one_time,
        "due_ts": due_ts,
        "enqueued_ts": now_ts,
        "enqueue_seq": schedule_id,
        "source": "one_time" if is_one_time else "recurring",
        "expected_duration_seconds": 5,
    }
    with sched._queue_lock:
        sched._announcement_queue.append(item)
        sched._announcement_enqueued_keys.add(key)


def _mock_db_for_queue(monkeypatch, *, one_time_status: str = "queued") -> MagicMock:
    """One-time schedule DB çağrılarını sahte veriyle yanıtla."""
    monkeypatch.setattr(
        scheduler_module.db,
        "update_one_time_schedule_status",
        MagicMock(),
    )
    monkeypatch.setattr(
        scheduler_module.db,
        "get_one_time_schedule",
        lambda sid: {"status": one_time_status},
    )


# ─── SENARYO 1: Ezan vakti sessizliği ──────────────────────────────────────


class TestPrayerTimeSilence:
    """Ezan sırasında anons bloklanır, ezan bitince çalınır."""

    def test_announcement_is_not_dispatched_during_prayer(self, monkeypatch):
        """Ezan aktifken (silence_blocked=True) anons oynatılmamalı."""
        sched = _make_scheduler()
        _mock_db_for_queue(monkeypatch)
        player = _fake_player()
        monkeypatch.setattr("scheduler.get_player", lambda: player)

        play_calls = []
        monkeypatch.setattr(
            sched,
            "_play_media",
            lambda *a, **kw: play_calls.append(kw.get("schedule_id")) or True,
        )

        _inject_item(sched, schedule_id=1, is_one_time=True, age_seconds=0)

        # 5 tick boyunca ezan aktif
        for _ in range(5):
            sched._process_announcement_queue(
                outside_working_hours=False, silence_blocked=True
            )

        assert len(play_calls) == 0, "Ezan sırasında anons oynatılmamalı"
        assert len(sched._announcement_queue) == 1, "Anons kuyrukta kalmalı, silinmemeli"

    def test_announcement_dispatched_immediately_after_prayer_ends(self, monkeypatch):
        """Ezan biter bitmez (silence_blocked=False) anons oynatılmalı."""
        sched = _make_scheduler()
        _mock_db_for_queue(monkeypatch)
        player = _fake_player()
        monkeypatch.setattr("scheduler.get_player", lambda: player)

        play_calls = []
        monkeypatch.setattr(
            sched,
            "_play_media",
            lambda *a, **kw: play_calls.append(kw.get("schedule_id")) or True,
        )

        _inject_item(sched, schedule_id=42, is_one_time=True, age_seconds=0)

        # Ezan aktif: 5 tick blok
        for _ in range(5):
            sched._process_announcement_queue(
                outside_working_hours=False, silence_blocked=True
            )
        assert len(play_calls) == 0

        # Ezan bitti: ilk tickte oynatılmalı
        sched._process_announcement_queue(outside_working_hours=False, silence_blocked=False)

        assert len(play_calls) == 1, "Ezan bittikten sonra anons oynatılmalı"
        assert play_calls[0] == 42

    def test_announcement_survives_stale_drop_during_8min_prayer(self, monkeypatch):
        """Ezan ~8 dk sürer; item < 15 dk stale eşiği içindeyse düşürülmemeli."""
        sched = _make_scheduler()
        sched._announcement_max_delay_seconds = 900  # 15 dk
        _mock_db_for_queue(monkeypatch)
        monkeypatch.setattr("scheduler.get_player", lambda: _fake_player())

        play_calls = []
        monkeypatch.setattr(
            sched,
            "_play_media",
            lambda *a, **kw: play_calls.append(True) or True,
        )

        # Anons ezan başında kuyruğa girdi — 8 dk önce planlanmış gibi (480 sn)
        _inject_item(sched, schedule_id=7, is_one_time=True, age_seconds=480)

        # Ezan bitti, stale drop çalışacak (480 < 900 → düşürülmemeli)
        sched._process_announcement_queue(outside_working_hours=False, silence_blocked=False)

        assert len(play_calls) == 1, (
            "8 dk bekleyen anons 15 dk stale eşiğinin altında, oynatılmalı"
        )

    def test_announcement_dropped_if_prayer_exceeds_max_delay(self, monkeypatch):
        """Anons max_delay_seconds'dan eski ise stale drop onu siler."""
        sched = _make_scheduler()
        sched._announcement_max_delay_seconds = 300  # 5 dk eşik (test için kısa)
        monkeypatch.setattr(
            scheduler_module.db, "update_one_time_schedule_status", MagicMock()
        )
        monkeypatch.setattr("scheduler.get_player", lambda: _fake_player())
        monkeypatch.setattr(
            sched, "_play_media", lambda *a, **kw: True
        )

        # Anons 6 dk önce planlanmış → max_delay (5 dk) aşıldı
        _inject_item(sched, schedule_id=9, is_one_time=True, age_seconds=360)

        sched._process_announcement_queue(outside_working_hours=False, silence_blocked=False)

        # Stale drop devreye girmeli, oynatılmamalı
        assert sched._announcement_queue_counters["dropped_stale"] == 1


# ─── SENARYO 2: Mesai dışı birikim ─────────────────────────────────────────


class TestWorkingHoursBacklog:
    """Mesai dışı kuyruklanan anonslar mesai başında sırayla oynatılır."""

    def test_10_announcements_stay_queued_outside_working_hours(self, monkeypatch):
        """Mesai dışında (outside_working_hours=True) anons oynatılmamalı."""
        sched = _make_scheduler()
        _mock_db_for_queue(monkeypatch)
        monkeypatch.setattr("scheduler.get_player", lambda: _fake_player())

        play_calls = []
        monkeypatch.setattr(
            sched,
            "_play_media",
            lambda *a, **kw: play_calls.append(True) or True,
        )

        for i in range(1, 11):
            _inject_item(sched, schedule_id=i, is_one_time=(i % 2 == 1), age_seconds=5)

        for _ in range(10):
            sched._process_announcement_queue(
                outside_working_hours=True, silence_blocked=False
            )

        assert len(play_calls) == 0, "Mesai dışında hiçbir anons oynatılmamalı"
        assert len(sched._announcement_queue) == 10, "Tüm 10 anons kuyrukta beklemeli"

    def test_10_fresh_items_survive_stale_drop(self, monkeypatch):
        """Mesai başlangıcında 14 dk önce kuyruğa giren anonslar stale drop'tan kurtulur."""
        sched = _make_scheduler()
        sched._announcement_max_delay_seconds = 900  # 15 dk
        monkeypatch.setattr(
            scheduler_module.db, "update_one_time_schedule_status", MagicMock()
        )

        for i in range(1, 11):
            _inject_item(sched, schedule_id=i, is_one_time=(i % 2 == 1), age_seconds=840)  # 14 dk

        with sched._queue_lock:
            sched._drop_stale_announcement_queue_items()

        assert len(sched._announcement_queue) == 10, (
            "14 dk önce kuyruğa giren anonslar 15 dk eşiği içinde, korunmalı"
        )
        assert sched._announcement_queue_counters["dropped_stale"] == 0

    def test_stale_drop_removes_both_one_time_and_recurring(self, monkeypatch):
        """Mevcut davranış: stale drop one-time ve recurring ayrımı yapmıyor.

        Bu test mevcut durumu belgeler. One-time anonslar için bu davranış
        'sessiz kayıp' riski oluşturur: mesai öncesi planlanan kritik anonslar
        mesai başında stale sayılıp silinebilir.
        """
        sched = _make_scheduler()
        sched._announcement_max_delay_seconds = 60  # 1 dk eşik (test hızı için)
        monkeypatch.setattr(
            scheduler_module.db, "update_one_time_schedule_status", MagicMock()
        )

        stale_age = 120  # 2 dk → eşik aşıldı

        for i in range(1, 6):
            _inject_item(sched, schedule_id=i, is_one_time=True, age_seconds=stale_age)
        for i in range(6, 11):
            _inject_item(sched, schedule_id=i, is_one_time=False, age_seconds=stale_age)

        assert len(sched._announcement_queue) == 10

        with sched._queue_lock:
            sched._drop_stale_announcement_queue_items()

        # Mevcut davranış: 10/10 düşürülür (one-time dahil)
        assert len(sched._announcement_queue) == 0, (
            "Mevcut kod: one-time ve recurring aynı eşikle stale drop yapıyor"
        )
        assert sched._announcement_queue_counters["dropped_stale"] == 10

    def test_announcements_dispatch_sequentially_one_per_tick(self, monkeypatch):
        """Mesai başlayınca anonslar her tickte bir tane oynatılır — aynı anda değil.

        Bu test back-to-back oynatma davranışını doğrular:
        10 anons varsa 10 tick gerekir (her biri ~gap_seconds aralıklı).
        """
        sched = _make_scheduler()
        sched._announcement_gap_seconds = 0

        session_counter = [0]

        def fake_player():
            p = MagicMock()
            p.is_playing = False          # her tickte "bitti" görünür → current temizlenir
            p._playback_session = session_counter[0]
            p._playlist_active = False
            return p

        monkeypatch.setattr("scheduler.get_player", fake_player)
        _mock_db_for_queue(monkeypatch)

        play_order = []

        def fake_play(filepath, *, schedule_id, is_one_time, is_announcement):
            play_order.append(schedule_id)
            session_counter[0] += 1
            return True

        monkeypatch.setattr(sched, "_play_media", fake_play)

        # 5 one-time + 5 recurring, hepsi 5 sn önce planlanmış (taze)
        for i in range(1, 6):
            _inject_item(sched, schedule_id=i, is_one_time=True, age_seconds=5)
        for i in range(6, 11):
            _inject_item(sched, schedule_id=i, is_one_time=False, age_seconds=5)

        dispatched_per_tick = []
        for _ in range(15):
            before = len(play_order)
            sched._process_announcement_queue(
                outside_working_hours=False, silence_blocked=False
            )
            dispatched_per_tick.append(len(play_order) - before)

        assert len(play_order) == 10, "Tüm 10 anons oynatılmalı"
        assert max(dispatched_per_tick) == 1, (
            "Hiçbir tickte 1'den fazla anons dispatch edilmemeli — sıralı oynatma"
        )

    def test_policy_unblock_does_not_lose_fifo_order(self, monkeypatch):
        """Mesai başladığında anonslar kuyruğa giriş sırasıyla (FIFO) oynatılır."""
        sched = _make_scheduler()
        sched._announcement_gap_seconds = 0

        session_counter = [0]

        def fake_player():
            p = MagicMock()
            p.is_playing = False
            p._playback_session = session_counter[0]
            p._playlist_active = False
            return p

        monkeypatch.setattr("scheduler.get_player", fake_player)
        _mock_db_for_queue(monkeypatch)

        play_order = []

        def fake_play(filepath, *, schedule_id, is_one_time, is_announcement):
            play_order.append(schedule_id)
            session_counter[0] += 1
            return True

        monkeypatch.setattr(sched, "_play_media", fake_play)

        # id=10 en erken (50 sn önce), id=1 en geç (5 sn önce)
        for i in range(10, 0, -1):
            _inject_item(sched, schedule_id=i, is_one_time=False, age_seconds=i * 5)

        for _ in range(15):
            sched._process_announcement_queue(
                outside_working_hours=False, silence_blocked=False
            )

        assert play_order == list(range(10, 0, -1)), (
            "En eski due_ts önce oynatılmalı (FIFO sırası korunmalı)"
        )


# ─── SENARYO 3: _check_*_schedules kuyruğa almama (fix doğrulaması) ────────


class TestCheckSchedulesOutsideWorkingHours:
    """Mesai dışında _check_*_schedules anons kuyruğa almamalı.

    Fix: announcement one-time ve recurring için outside_working_hours=True
    iken _queue_announcement çağrılmaz. Non-announcement içerikler için bu
    mantık zaten vardı; bu testler announcement yolunu doğrular.
    """

    def test_one_time_announcement_not_queued_outside_working_hours(self, monkeypatch):
        """Mesai dışındaki one-time anons kuyruğa girmez, DB'de cancelled olur."""
        sched = _make_scheduler()

        update_status = MagicMock()
        monkeypatch.setattr(scheduler_module.db, "update_one_time_schedule_status", update_status)

        # now = 07:00 UTC, schedule = 30 sn önce (time_diff=30 → [0,120] aralığında)
        fixed_now = datetime(2026, 3, 28, 7, 0, 0, tzinfo=timezone.utc)
        scheduled_dt = fixed_now - timedelta(seconds=30)
        monkeypatch.setattr("scheduler.now_utc", lambda: fixed_now)

        monkeypatch.setattr(
            scheduler_module.db,
            "get_pending_one_time_schedules",
            lambda: [{
                "id": 101,
                "scheduled_datetime": scheduled_dt,
                "filename": "mesai_disi.mp3",
                "filepath": "/tmp/mesai_disi.mp3",
                "media_id": 1,
                "media_type": "announcement",
            }],
        )

        queue_calls = []
        monkeypatch.setattr(
            sched, "_queue_announcement", lambda **kw: queue_calls.append(kw)
        )

        sched._check_one_time_schedules(outside_working_hours=True, silence_blocked=False)

        assert len(queue_calls) == 0, "Mesai dışında kuyruğa girmemeli"
        update_status.assert_called_once_with(101, "cancelled")

    def test_one_time_announcement_queued_inside_working_hours(self, monkeypatch):
        """Mesai içindeki one-time anons normal şekilde kuyruğa girer."""
        sched = _make_scheduler()

        monkeypatch.setattr(
            scheduler_module.db, "update_one_time_schedule_status", MagicMock()
        )

        fixed_now = datetime(2026, 3, 28, 10, 0, 0, tzinfo=timezone.utc)
        scheduled_dt = fixed_now - timedelta(seconds=30)
        monkeypatch.setattr("scheduler.now_utc", lambda: fixed_now)

        monkeypatch.setattr(
            scheduler_module.db,
            "get_pending_one_time_schedules",
            lambda: [{
                "id": 202,
                "scheduled_datetime": scheduled_dt,
                "filename": "mesai_ici.mp3",
                "filepath": "/tmp/mesai_ici.mp3",
                "media_id": 2,
                "media_type": "announcement",
            }],
        )

        # resolve_duration_seconds çağrısını mock'la
        monkeypatch.setattr(
            "scheduler.resolve_duration_seconds", lambda media_id: 30
        )

        queue_calls = []
        monkeypatch.setattr(
            sched, "_queue_announcement", lambda **kw: queue_calls.append(kw)
        )

        sched._check_one_time_schedules(outside_working_hours=False, silence_blocked=False)

        assert len(queue_calls) == 1, "Mesai içinde kuyruğa girmeli"
        assert queue_calls[0]["schedule_id"] == 202

    def test_recurring_announcement_not_queued_outside_working_hours(self, monkeypatch):
        """Mesai dışındaki recurring anons kuyruğa girmez, sessizce atlanır."""
        sched = _make_scheduler()

        # Pazartesi 07:00 — specific_times ile eşleşiyor
        fixed_now = datetime(2026, 3, 23, 7, 0, 0)  # 2026-03-23 Pazartesi
        monkeypatch.setattr("scheduler.now_local", lambda: fixed_now)

        monkeypatch.setattr(
            scheduler_module.db,
            "get_active_recurring_schedules",
            lambda: [{
                "id": 301,
                "days_of_week": [0],           # Pazartesi
                "specific_times": ["07:00"],
                "interval_minutes": None,
                "filename": "recurring_anons.mp3",
                "filepath": "/tmp/recurring_anons.mp3",
                "media_id": 3,
                "media_type": "announcement",
            }],
        )

        queue_calls = []
        monkeypatch.setattr(
            sched, "_queue_announcement", lambda **kw: queue_calls.append(kw)
        )

        sched._check_recurring_schedules(outside_working_hours=True, silence_blocked=False)

        assert len(queue_calls) == 0, "Mesai dışında recurring anons kuyruğa girmemeli"

    def test_recurring_announcement_queued_inside_working_hours(self, monkeypatch):
        """Mesai içindeki recurring anons normal şekilde kuyruğa girer."""
        sched = _make_scheduler()

        fixed_now = datetime(2026, 3, 23, 10, 0, 0)  # Pazartesi 10:00
        monkeypatch.setattr("scheduler.now_local", lambda: fixed_now)

        monkeypatch.setattr(
            scheduler_module.db,
            "get_active_recurring_schedules",
            lambda: [{
                "id": 401,
                "days_of_week": [0],
                "specific_times": ["10:00"],
                "interval_minutes": None,
                "filename": "recurring_mesai_ici.mp3",
                "filepath": "/tmp/recurring_mesai_ici.mp3",
                "media_id": 4,
                "media_type": "announcement",
            }],
        )

        monkeypatch.setattr(
            "scheduler.resolve_duration_seconds", lambda media_id: 30
        )

        queue_calls = []
        monkeypatch.setattr(
            sched, "_queue_announcement", lambda **kw: queue_calls.append(kw)
        )

        sched._check_recurring_schedules(outside_working_hours=False, silence_blocked=False)

        assert len(queue_calls) == 1, "Mesai içinde recurring anons kuyruğa girmeli"
        assert queue_calls[0]["schedule_id"] == 401

    def test_ezan_silence_still_queues_announcement(self, monkeypatch):
        """Ezan sessizliği (outside_working_hours=False, silence_blocked=True) anons kuyruğa alır.

        Bu fix sadece outside_working_hours kontrolü ekliyor.
        Ezan davranışı değişmemeli — ezan sırasında kuyruğa girip ezan sonrası çalmalı.
        """
        sched = _make_scheduler()

        monkeypatch.setattr(
            scheduler_module.db, "update_one_time_schedule_status", MagicMock()
        )

        fixed_now = datetime(2026, 3, 28, 12, 0, 0, tzinfo=timezone.utc)
        scheduled_dt = fixed_now - timedelta(seconds=30)
        monkeypatch.setattr("scheduler.now_utc", lambda: fixed_now)

        monkeypatch.setattr(
            scheduler_module.db,
            "get_pending_one_time_schedules",
            lambda: [{
                "id": 501,
                "scheduled_datetime": scheduled_dt,
                "filename": "ezan_anons.mp3",
                "filepath": "/tmp/ezan_anons.mp3",
                "media_id": 5,
                "media_type": "announcement",
            }],
        )

        monkeypatch.setattr(
            "scheduler.resolve_duration_seconds", lambda media_id: 30
        )

        queue_calls = []
        monkeypatch.setattr(
            sched, "_queue_announcement", lambda **kw: queue_calls.append(kw)
        )

        # Ezan aktif: outside_working_hours=False, silence_blocked=True
        sched._check_one_time_schedules(outside_working_hours=False, silence_blocked=True)

        assert len(queue_calls) == 1, (
            "Ezan sırasında anons kuyruğa girmeli (ezan bitince çalacak)"
        )
        assert queue_calls[0]["schedule_id"] == 501
