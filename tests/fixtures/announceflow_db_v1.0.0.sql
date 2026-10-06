PRAGMA foreign_keys=OFF;
BEGIN TRANSACTION;
CREATE TABLE media_files (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            filename TEXT NOT NULL,
            filepath TEXT NOT NULL,
            media_type TEXT NOT NULL CHECK(media_type IN ('music', 'announcement')),
            duration_seconds INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
INSERT INTO media_files VALUES(1,'song.mp3','/home/admin/announceflow/media/music/song.mp3','music',180,'2026-10-06 12:51:11');
INSERT INTO media_files VALUES(2,'anons.mp3','/home/admin/announceflow/media/announcements/anons.mp3','announcement',12,'2026-10-06 12:51:11');
CREATE TABLE one_time_schedules (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            media_id INTEGER NOT NULL,
            scheduled_datetime TIMESTAMP NOT NULL,
            reason TEXT,
            status TEXT DEFAULT 'pending' CHECK(status IN ('pending', 'played', 'cancelled')),
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (media_id) REFERENCES media_files (id) ON DELETE CASCADE
        );
INSERT INTO one_time_schedules VALUES(1,2,'2026-03-01T10:30:00','kampanya','pending','2026-10-06 12:51:11');
CREATE TABLE recurring_schedules (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            media_id INTEGER NOT NULL,
            days_of_week TEXT NOT NULL,
            start_time TEXT NOT NULL,
            end_time TEXT,
            interval_minutes INTEGER DEFAULT 0,
            specific_times TEXT,
            reason TEXT,
            is_active INTEGER DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (media_id) REFERENCES media_files (id) ON DELETE CASCADE
        );
INSERT INTO recurring_schedules VALUES(1,2,'[0, 1, 2, 3, 4]','09:00',NULL,0,'["09:00", "13:00"]','acilis',1,'2026-10-06 12:51:11');
INSERT INTO recurring_schedules VALUES(2,2,'[5, 6]','10:00','18:00',30,NULL,NULL,1,'2026-10-06 12:51:11');
CREATE TABLE playback_state (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            current_media_id INTEGER,
            position_seconds REAL DEFAULT 0,
            is_playing INTEGER DEFAULT 0,
            volume INTEGER DEFAULT 80,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, playlist_json TEXT, playlist_index INTEGER DEFAULT -1, playlist_loop INTEGER DEFAULT 1, playlist_active INTEGER DEFAULT 0,
            FOREIGN KEY (current_media_id) REFERENCES media_files (id) ON DELETE SET NULL
        );
INSERT INTO playback_state VALUES(1,NULL,0.0,0,37,'2026-10-06 12:51:11','["/home/admin/announceflow/media/music/song.mp3"]',0,1,1);
INSERT INTO sqlite_sequence VALUES('media_files',2);
INSERT INTO sqlite_sequence VALUES('one_time_schedules',1);
INSERT INTO sqlite_sequence VALUES('recurring_schedules',2);
CREATE INDEX idx_one_time_schedules_media_id
        ON one_time_schedules(media_id)
    ;
CREATE INDEX idx_one_time_schedules_status
        ON one_time_schedules(status)
    ;
CREATE INDEX idx_one_time_schedules_datetime
        ON one_time_schedules(scheduled_datetime)
    ;
CREATE INDEX idx_recurring_schedules_media_id
        ON recurring_schedules(media_id)
    ;
CREATE INDEX idx_recurring_schedules_active
        ON recurring_schedules(is_active)
    ;
COMMIT;
