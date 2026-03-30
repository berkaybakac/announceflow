<div align="center">

# AnnounceFlow

**Production-deployed in-store audio control system**

[![Python](https://img.shields.io/badge/Python-3.9+-3776AB?logo=python&logoColor=white)](https://python.org)
[![Flask](https://img.shields.io/badge/Flask-3.0-000000?logo=flask)](https://flask.palletsprojects.com)
[![SQLite](https://img.shields.io/badge/SQLite-WAL_Mode-003B57?logo=sqlite)](https://sqlite.org)
[![Raspberry Pi](https://img.shields.io/badge/Raspberry%20Pi%204-Deployed-A22846?logo=raspberrypi)](https://raspberrypi.org)
[![Tests](https://img.shields.io/badge/Tests-Pytest_Suite-2ea44f)](#testing)
[![Version](https://img.shields.io/badge/Version-2.2.0-blue)](https://github.com/berkaybakac/announceflow/releases/tag/v2.2.0)
[![License](https://img.shields.io/badge/License-Proprietary-red)](#license)

*Running in a real store environment since January 2026*

</div>

---

## At a Glance

> **What:** A web-controlled audio system for retail stores: playlists, scheduled announcements, live streaming, prayer-time automation.
>
> **Where:** Runs on a Raspberry Pi 4 in-store, accessed via web panel and a Windows desktop agent over LAN.
>
> **Why it's hard:** LAN instability, power loss, no on-site IT support. The system must keep audio running without manual intervention during business hours.
>
> **Scale:** Single-branch retail, one Pi per store, deployed and operating since January 2026.
>
> **Tested:** Broad pytest coverage for scheduler timing, prayer-time overlap, stream health monitoring, media validation, and agent flows.

---

## Overview

AnnounceFlow is an in-store audio control platform built for single-branch retail operations on Raspberry Pi 4.
It handles background playlist playback, scheduled announcements, prayer-time automation, live streaming from a Windows sender, and real-time volume/mute control. Operators use a web panel for daily tasks; a Windows desktop agent handles technical setup.

The implementation is shaped by field constraints: LAN instability, power loss, limited on-site technical support, and the need for zero-downtime audio continuity during business hours.

Deployment is intentionally simple: SSH + rsync + systemd with manual release control.

---

## Technical Stack

| Layer | Technology |
|-------|------------|
| Backend | Python 3.9+, Flask 3.0, Waitress WSGI |
| Database | SQLite with WAL mode and repository pattern |
| Frontend | HTML5, CSS3, vanilla JavaScript, Jinja2 |
| Audio Engine | mpg123, FFmpeg, ALSA |
| Deployment | systemd, SSH, rsync |
| Hardware | Raspberry Pi 4 |
| Desktop Agent | Python, Tkinter, system tray (Windows) |

---

## Architecture

```text
Client Layer
├── Web Browser (responsive)
├── Windows Desktop Agent (system tray)
└── Mobile Browser
         │
         ▼  HTTP / REST API
┌───────────────────────────────────────────────────┐
│              Flask Application                    │
├───────────────────────────────────────────────────┤
│  Routes        │ Player      │ Scheduler          │
│  (Blueprints)  │ (mpg123)    │ (Queue-Lite)       │
├───────────────────────────────────────────────────┤
│  Volume        │ Slot Map    │ Audio Alert        │
│  Runtime       │ Service     │ Service            │
├───────────────────────────────────────────────────┤
│  Repositories  │ Conflict    │ External API       │
│  (SQLite/WAL)  │ Detection   │ (Diyanet)          │
└───────────────────────────────────────────────────┘
         │
         ▼
   Raspberry Pi 4 (systemd service)
```

---

## Core Modules

- `main.py`: service bootstrap, logging, startup recovery, and runtime wiring.
- `player.py`: audio playback engine, ALSA device selection, codec handling, and volume calibration.
- `scheduler.py`: one-time and recurring dispatch, queue-lite deduplication, and silence-policy integration.
- `routes/`: playback, scheduling, media, settings, and stream HTTP endpoints.
- `services/`: policy, config, slot-map, audio-alert, and runtime coordination logic.
- `agent/`: Windows tray agent, sender control plane, and operator-facing desktop integration.
- `tests/`: regression suite focused on field failures and timing-sensitive edge cases.

---

## Key Features

- **Playlist engine** with loop playback, track-end auto-advance, and resume after interruptions.
- **Scheduled announcements** (one-time and recurring) with conflict detection and queue-lite deduplication.
- **Prayer-time automation** via Diyanet API with local caching, overlap priority, and fail-safe silence.
- **Business-hours policy** with automatic mute behavior and configurable silence windows.
- **Real-time volume control** (0-100) with mute toggle and automatic restore after announcements.
- **Interactive timeline** with conflict badges, hover-to-highlight groups, and up to 500% zoom.
- **Live streaming** from Windows sender with heartbeat monitoring and audio alert evaluation.
- **Windows desktop agent** with hostname-first login, LAN fallback, state-aware tray icon, and panel-based distribution.

---

## API Design

REST API across dedicated Flask Blueprints with JSON payloads and session-based authentication. Covers playback, scheduling, stream lifecycle, media management, and health monitoring.

Full endpoint reference: [`docs/API.md`](docs/API.md)

---

## System Requirements

- Production target: Raspberry Pi 4 running Raspberry Pi OS or another Linux distribution with ALSA support.
- Development target: macOS or Linux for the web app; Windows is supported for the desktop agent.
- Python: 3.9+
- Required system packages: `ffmpeg`, `mpg123`, `python3-tk` on Linux or `python-tk` on macOS.
- Runtime config: optional `.env` overrides and `config.json`; the app can still boot with defaults.

---

## Deployment and Ops

### Quick Start (Development)

```bash
# Choose the system package block that matches your OS.

# Raspberry Pi OS / Ubuntu
sudo apt update
sudo apt install -y ffmpeg mpg123 python3-tk

# macOS
brew install ffmpeg mpg123 python-tk

python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Optional: start from editable defaults
cp config.example.json config.json
cp .env.example .env

python main.py
```

### Production

Deployed to Raspberry Pi via `deploy.sh` with two profiles: `standard` (routine) and `clean-delivery` (customer handoff with content wipe).

Full deployment guide, release workflow, hostname standard, and agent distribution flow: [`docs/OPERATIONS.md`](docs/OPERATIONS.md)

---

## Testing

```bash
python -m pytest -q
```

Verified locally with `pytest -q`: `677 passed, 14 skipped, 10 subtests passed` (March 30, 2026).

Test areas include: API and auth flows, stream lifecycle, agent login and discovery, schedule conflict detection, volume state contracts, mute restore sequences, announcement queue backlog, NTP clock skew handling, prayer-time overlap priority, scheduler loop errors, timeline slot rendering, slot map service, audio alert evaluation, media upload validation, and playback repository schema migration.

---

## Engineering Under Constraints

Running on a Raspberry Pi 4 in a retail store means dealing with SD card wear, power loss, LAN instability, non-technical operators, and no remote access. These are the engineering responses to real failures encountered in the field.

**Power loss recovery.**
Playlist state is persisted to SQLite after every operation. On boot, the system restores the previous playlist position but checks prayer-time and business-hours policy first. If the API call for prayer times fails, playback stays silent (fail-safe). Music never plays at the wrong time after an unexpected reboot.

**SD card longevity.**
SQLite runs in WAL mode to reduce random writes. Application logs are capped at 500 KB with 3 rotated backups. ffmpeg stream logs use a custom rotating writer capped at 2 MB with 5 backups. Partial files from failed media conversions are cleaned up immediately.

**Pi 4 audio calibration.**
The Raspberry Pi 4 analog output has a non-linear volume curve: hardware 70% is barely audible, hardware 100% is only +4 dB above that. A sqrt-based calibration maps UI 0-100% to the usable hardware range (70-100%), so operators get predictable volume control instead of a binary loud/silent knob.

**ALSA device auto-detection.**
Audio output device is discovered through a 6-step fallback chain: environment variable, USB DAC probe, headphone card match, then known defaults (`plughw:2,0`, `plughw:0,0`, `default`). Duplicates are skipped. This handles Pi setups with different audio hardware without manual configuration.

**Codec auto-fix.**
Operators upload voice recordings from WhatsApp or phone memos as `.mp3` files, but these are often AAC in an MP3 container. mpg123 can't play them. The system detects the codec mismatch via ffprobe and auto-converts to real MP3 before saving. Failed conversions clean up partial files.

**UDP jitter buffer.**
Live stream uses a 4 MB UDP FIFO buffer (configurable), which absorbs roughly 47 seconds of network jitter at mono 44.1 kHz. Overruns are non-fatal. This prevents audio dropouts during LAN congestion without requiring QoS configuration on the store's consumer-grade router.

**Subprocess lifecycle.**
Before starting a new mpg123 process, any orphan from a previous session is killed and waited on. ffmpeg stderr is drained by a dedicated daemon thread to prevent pipe buffer deadlock. Stream shutdown follows a SIGTERM-then-SIGKILL sequence with a 1-second grace window.

**Clock skew immunity.**
Schedule gap timers use `time.monotonic()` so NTP corrections don't cause announcement replays or skipped slots. Absolute schedule times use wall clock. All stored timestamps are UTC. Timezone handling falls back to UTC if the system timezone database is corrupted.

**Stream observability.**
Each stream session gets a correlation ID (`local-{pid}-{timestamp}`). The receiver tracks ALSA xruns, UDP overruns, demux errors, and network timeouts as counters, then emits a summary on session end. The web panel exposes these via `/api/stream/alerts` for real-time monitoring.

**Deduplication guards.**
Upload button double-clicks are caught by a 15-second dedup window keyed on `(filename, type, size)`. Scheduled announcements are deduped by `source:id:minute` to prevent the scheduler's 10 Hz tick from queuing duplicates. A 10-second gap is enforced between consecutive announcements.

---

## Common Environment Overrides

| Variable | Description |
|----------|-------------|
| `FLASK_SECRET_KEY` | Session security key |
| `ANNOUNCEFLOW_WEB_PORT` | Server port (default: 5001) |
| `ANNOUNCEFLOW_MEDIA_FOLDER` | Media storage path |
| `ANNOUNCEFLOW_SCHEDULER_INTERVAL_SECONDS` | Scheduler loop interval override |
| `ANNOUNCEFLOW_ALSA_DEVICE` | Preferred ALSA playback device |
| `ANNOUNCEFLOW_ALSA_CARD` | Preferred ALSA card for `amixer` volume control |
| `ANNOUNCEFLOW_LOG_DIR` | Custom directory for rotated logs |

See `.env.example` and `config.example.json` for the common local setup defaults.

---

## What This Project Demonstrates

- Running a production service on constrained edge hardware with real uptime requirements.
- Designing for failure: power loss, network drops, clock skew, codec mismatches, orphan processes.
- Bridging the gap between operator simplicity and engineering depth (non-technical staff use it daily).
- A broad regression suite covering field-observed edge cases, not theoretical scenarios.

---

## Design Trade-offs

| Decision | Rationale |
|----------|-----------|
| **Vanilla JavaScript** over React/Vue | No build step, no node dependency on Pi. Keeps deployment a single rsync. The UI is operator-focused, not a complex SPA. |
| **SQLite (WAL)** over PostgreSQL | No database server process on a 4 GB Pi. WAL mode provides the read concurrency needed for panel + scheduler. |
| **Session-based auth** over JWT/OAuth | Single-user system on a private LAN. Token infrastructure would add complexity with no security benefit in this threat model. |
| **No containerization** | Docker on Pi 4 adds memory overhead and startup latency. systemd gives direct hardware access (ALSA, GPIO) and simpler debugging. |
| **Manual deploy** (`deploy.sh` + systemd) | One Pi, one store. CI/CD pipeline would be overengineering. The deploy script is idempotent with health checks and release stamping. |
| **No RBAC** | Single operator per store. Role separation adds UX friction for the target user (store staff, not IT teams). |

---

## License

Proprietary. All rights reserved. See `LICENSE`.
