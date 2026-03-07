<div align="center">

# AnnounceFlow

**Autonomous Audio Management System for Commercial Spaces**

[![Python](https://img.shields.io/badge/Python-3.9+-3776AB?logo=python&logoColor=white)](https://python.org)
[![Flask](https://img.shields.io/badge/Flask-3.0-000000?logo=flask)](https://flask.palletsprojects.com)
[![SQLite](https://img.shields.io/badge/SQLite-Database-003B57?logo=sqlite)](https://sqlite.org)
[![HTML5](https://img.shields.io/badge/HTML5-E34F26?logo=html5&logoColor=white)](https://developer.mozilla.org/en-US/docs/Web/HTML)
[![CSS3](https://img.shields.io/badge/CSS3-1572B6?logo=css3&logoColor=white)](https://developer.mozilla.org/en-US/docs/Web/CSS)
[![JavaScript](https://img.shields.io/badge/JavaScript-F7DF1E?logo=javascript&logoColor=black)](https://developer.mozilla.org/en-US/docs/Web/JavaScript)
[![Raspberry Pi](https://img.shields.io/badge/Raspberry%20Pi%204-Deployed-A22846?logo=raspberrypi)](https://raspberrypi.org)

*Production-deployed system running 24/7 in a fast-food restaurant since January 2026*

</div>

---

## Overview

AnnounceFlow is an embedded audio system designed for restaurants and retail stores. It runs on Raspberry Pi 4 and provides automated music playback, prayer time integration via Turkish Diyanet API, and scheduled announcements with zero daily maintenance.

The system was built to solve real operational problems: staff forgetting to turn on music, audio playing during culturally sensitive times, and complete state loss after power outages.

---

## Technical Stack

| Layer | Technology |
|-------|------------|
| Backend | Python 3.9+, Flask 3.0, Waitress WSGI |
| Database | SQLite with Repository Pattern |
| Frontend | HTML5, CSS3, Vanilla JavaScript, Jinja2 |
| Audio Engine | mpg123, FFmpeg |
| Deployment | systemd, SSH, rsync |
| Hardware | Raspberry Pi 4 (2GB RAM) |

---

## Architecture

```
Client Layer
├── Web Browser (responsive)
├── Windows Desktop Agent (system tray)
└── Mobile Browser
         │
         ▼  HTTP / REST API
┌─────────────────────────────────────────┐
│          Flask Application              │
├─────────────────────────────────────────┤
│  Routes       │ Player     │ Scheduler  │
│  (Blueprints) │ (mpg123)   │ (Custom)   │
├─────────────────────────────────────────┤
│  Repositories │ Services   │ External   │
│  (SQLite)     │ (Logic)    │ API        │
└─────────────────────────────────────────┘
         │
         ▼
   Raspberry Pi 4 (systemd service)
```

---

## API Design

REST API with Flask Blueprints. JSON request/response format.

**Endpoints:**

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | /api/health | Health check (no auth) |
| POST | /api/play | Play media file |
| POST | /api/stop | Stop playback |
| POST | /api/volume | Set volume (0-100) |
| GET | /api/now-playing | Current player state |
| GET | /api/media/music | List music files |
| POST | /api/playlist/start | Start playlist loop |

**Authentication:** Session-based with Flask session. Secret key managed via environment variables.

---

## Database Schema

SQLite with foreign key relationships and indexed queries.

**Tables:**
- `media_files` - Uploaded audio files with duration metadata
- `one_time_schedules` - Single-use scheduled announcements
- `recurring_schedules` - Repeating schedules (daily, weekly patterns)
- `playback_state` - Current player state for crash recovery

**Data Access:** Repository pattern implementation with `MediaRepository`, `ScheduleRepository`, and `PlaybackRepository` classes wrapping raw SQL queries.

---

## Key Features

**Audio Management**
- Playlist playback with loop support
- Resume from exact position after interruption
- Multi-format support (MP3, WAV, FLAC, M4A) via FFmpeg conversion

**External API Integration**
- Turkish Diyanet API for prayer times
- 7-day cache for offline operation
- Location-based queries (81 provinces, 900+ districts)

**Scheduling**
- One-time announcements for specific datetime
- Recurring announcements with daily/weekly patterns
- Business hours automation with auto-mute

**Reliability**
- State persistence in SQLite database
- Automatic recovery after power failure
- systemd service with restart policy

---

## Project Structure

```
announceflow/
├── main.py                 # Entry point
├── web_panel.py            # Flask app, auth routes
├── player.py               # Audio engine (mpg123/pygame)
├── scheduler.py            # Time-based job runner
├── prayer_times.py         # Diyanet API client
├── logger.py               # Rotating file logger
│
├── routes/                 # Flask Blueprints
│   ├── player_routes.py    # Playback control API
│   ├── media_routes.py     # File upload/delete
│   ├── schedule_routes.py  # Schedule CRUD
│   ├── playlist_routes.py  # Playlist control
│   └── settings_routes.py  # Configuration API
│
├── database/               # Data access layer
│   ├── __init__.py         # Schema, migrations
│   ├── base_repository.py  # Abstract base class
│   ├── media_repository.py
│   ├── schedule_repository.py
│   └── playback_repository.py
│
├── services/               # Business logic
├── templates/              # Jinja2 HTML templates
├── utils/                  # Helper functions, decorators
├── tests/                  # API tests
├── agent/                  # Windows desktop application
│
├── deploy.sh               # Remote deployment script
└── requirements.txt        # Python dependencies
```

---

## Deployment

**Development:**
```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python main.py
```

**Production (Raspberry Pi):**
```bash
./deploy.sh pi4.local
```

The deployment script handles file synchronization via rsync, dependency installation, systemd service configuration, automatic restart, and health check verification.

---

## Testing

```bash
python -m pytest tests/test_api.py -v
```

Test coverage includes: health endpoint, authentication flow, volume control, player state, media library operations, playlist control, and page rendering.

### Windows Agent Diagnostic Bundle

If stream start fails on a Windows 10/11 target machine, collect logs from that same machine.

Option A (helper files, easiest):

Copy these files from your Mac repo to the Windows machine:

- `agent/dist/AnnounceFlowAgent.exe`
- `scripts/preflight_windows_audio.ps1`
- `scripts/preflight_windows_audio.cmd`
- `scripts/collect_windows_agent_logs.ps1`
- `scripts/collect_windows_agent_logs.cmd`

Run either:

```cmd
collect_windows_agent_logs.cmd
```

or:

```powershell
powershell -ExecutionPolicy Bypass -File .\collect_windows_agent_logs.ps1 -LastMinutes 60
```

Option B (no extra file copy, direct one-liner in PowerShell):

```powershell
$logs="$env:LOCALAPPDATA\AnnounceFlow\logs"; $out="$env:TEMP\AnnounceFlowDiag"; New-Item -ItemType Directory -Force -Path $out | Out-Null; $ts=Get-Date -Format "yyyyMMdd_HHmmss"; $zip="$out\agent_logs_$ts.zip"; Compress-Archive -Path "$logs\*" -DestinationPath $zip -Force; Write-Host $zip
```

Output:

- Zip file under `%TEMP%\AnnounceFlowDiag\`
- Includes `agent_stream.log`, `agent.log`, and `stream_attempt_*.json` (if present)

### Stream Preflight Checks

Before manual end-to-end testing, run platform checks:

- Windows target machine (Audio services + default device + local log path):

```cmd
preflight_windows_audio.cmd
```

or:

```powershell
powershell -ExecutionPolicy Bypass -File .\preflight_windows_audio.ps1
```

- Pi receiver machine (ffmpeg/ALSA/receiver smoke):

```bash
./scripts/preflight_pi_receiver.sh /home/admin/announceflow
```

The scripts print PASS/WARN/FAIL and generate a report file path.

### Stream Logs (Quick)

`cd /home/admin/announceflow` sonrası en sık kullanılan komutlar:

```bash
# Olay ozeti (start/stop/receiver/stop-reason)
python3 scripts/events_query.py --file logs/events.jsonl \
  --since "2026-03-07T09:00:00Z" \
  --event stream_started --event stream_stopped \
  --event stream_receiver_started --event stream_receiver_summary \
  --event stream_receiver_udp_overrun --event stream_receiver_alsa_xrun \
  --event stream_receiver_stop_reason --summary
```

```bash
# Belirli correlation_id
python3 scripts/events_query.py --file logs/events.jsonl \
  --since "2026-03-07T09:00:00Z" \
  --contains "agent-..." --limit 200
```

```bash
# Stream telemetry tablosu
python3 scripts/stream_telemetry_report.py --file logs/events.jsonl \
  --since "2026-03-07T09:00:00Z" --limit 300
```

```bash
# FFmpeg receiver hatalari (overrun/xrun/bind/immediate-exit)
LC_ALL=C grep -aEni \
"Circular buffer overrun|ALSA buffer xrun|Immediate exit requested|bind failed|Error opening input" \
logs/stream_receiver_ffmpeg.log | tail -n 200
```

```bash
# Servis logu
journalctl -u announceflow --since "2026-03-07 09:00:00" --no-pager | tail -n 400
```

Stop-reason telemetry `reason` degerleri:
`graceful`, `force_kill`, `already_stopping`, `already_stopped`, `force_kill_timeout`, `error`.

---

## Environment Configuration

| Variable | Description |
|----------|-------------|
| FLASK_SECRET_KEY | Session security key |
| ANNOUNCEFLOW_WEB_PORT | Server port (default: 5001) |
| ANNOUNCEFLOW_MEDIA_FOLDER | Media storage path |

---

## What This Project Demonstrates

- REST API design and implementation with Flask
- Relational database operations with SQL and foreign keys
- Repository pattern for data access abstraction
- Session-based authentication with protected routes
- External API integration and caching strategy
- Server-side rendering with Jinja2 templating
- Linux service management with systemd
- Remote deployment automation via SSH
- Real-world production operation and maintenance

---

## Limitations

This project was built as a practical solution for a specific use case. The following were intentionally kept simple or not implemented:

- Frontend uses vanilla JavaScript; no React, Vue, or similar framework
- SQLite database; not PostgreSQL or MySQL
- Session-based authentication; no JWT or OAuth implementation
- No Docker containerization
- No CI/CD pipeline; manual deployment via script
- Single-user system; no role-based access control

---

## License

Proprietary. All rights reserved.

---

<div align="center">

*Built for reliability. Running in production.*

</div>
