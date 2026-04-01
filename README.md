<div align="center">

# AnnounceFlow

**Production-deployed in-store audio control system**

[![Python](https://img.shields.io/badge/Python-3.9+-3776AB?logo=python&logoColor=white)](https://python.org)
[![Flask](https://img.shields.io/badge/Flask-3.0-000000?logo=flask)](https://flask.palletsprojects.com)
[![SQLite](https://img.shields.io/badge/SQLite-WAL_Mode-003B57?logo=sqlite)](https://sqlite.org)
[![Raspberry Pi](https://img.shields.io/badge/Raspberry%20Pi%204-Deployed-A22846?logo=raspberrypi)](https://raspberrypi.org)
[![Tests](https://img.shields.io/badge/Tests-Pytest_Suite-2ea44f)](#testing)
[![License](https://img.shields.io/badge/License-Proprietary-red)](#license)

*Validated on Raspberry Pi 4 (1 GB RAM) in a real store environment since January 2026*

</div>

---

## At a Glance

> **What:** A web-controlled audio system for retail stores: playlists, scheduled announcements, live streaming, prayer-time automation.
>
> **Where:** Runs on a Raspberry Pi 4 in-store, accessed via web panel and a Windows desktop agent over LAN.
>
> **Why it's hard:** LAN instability, power loss, no on-site IT support. The system must keep audio running without manual intervention during business hours.
>
> **Scale:** Single-branch retail, one Pi per store.
>
> **Tested:** ~700 test cases across 140+ files.

---

## Overview

AnnounceFlow is an in-store audio control platform built for single-branch retail operations on Raspberry Pi 4.
It handles background playlist playback, scheduled announcements, prayer-time automation, live streaming from a Windows sender, and real-time volume/mute control. Operators use a web panel for daily tasks; a Windows desktop agent handles technical setup.

The implementation is shaped by field constraints: LAN instability, power loss, limited on-site technical support, and the need for unattended audio continuity during business hours.

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

Covers both happy paths and field-observed failure modes across scheduling, stream lifecycle, agent flows, volume contracts, prayer-time overlap, media validation, and more.

---

### Field Diagnostics

Quickly check the health and streaming quality of the device from the terminal or browser:

- **Terminal:** `python3 diagnose.py` (Summarizes the last 60 minutes)
- **Custom Duration:** `python3 diagnose.py 1440` (Analyzes the last 24 hours)
- **Browser/API:** `http://<device-ip>:5001/api/diagnose` (Returns JSON report)

---

## Engineering Under Constraints

**Power loss and policy-safe recovery.**
Playlist state is persisted after every operation. On boot, restoration is gated by prayer-time and business-hours policy, and unknown prayer data resolves to silence instead of accidental playback. This prevents unintended playback after an unexpected reboot.

**Priority-aware scheduling on a low-resource device.**
One-time and recurring announcements share a queue-lite scheduler with overlap handling, silence-policy checks, duplicate suppression, and monotonic timing guards. The focus is correctness under restarts, clock shifts, and repeated operator actions.

**Defensive media pipeline for real-world voice files.**
Operators upload audio exported from phones and messaging apps, not curated studio assets. The system detects mislabeled codecs, normalizes incompatible voice files before playback, and cleans up partial conversion output on failure.

**Resilient stream control plane.**
The web panel, receiver process, and Windows agent coordinate stream ownership through heartbeat-driven liveness checks, pause/resume transitions, and health alerts. This keeps live audio control recoverable even when the sender or network behaves badly.

**Operational durability on edge hardware.**
SQLite WAL mode, rotated logs, subprocess cleanup, fallback audio-device selection, and field diagnostics are treated as first-class concerns. The goal is a system that keeps working on-site, not just a panel that works in a clean dev environment.

Detailed implementation notes, lower-level measurements, and exact runtime heuristics: [`docs/RELIABILITY.md`](docs/RELIABILITY.md)

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

## Design Trade-offs

| Decision | Rationale |
|----------|-----------|
| **Vanilla JavaScript** over React/Vue | No build step, no node dependency on Pi. Keeps deployment a single rsync. The UI is operator-focused, not a complex SPA. |
| **SQLite (WAL)** over PostgreSQL | No database server process on a low-memory Pi-class device. WAL mode provides the read concurrency needed for panel + scheduler. |
| **Session-based auth** over JWT/OAuth | Single-user system on a private LAN. Token infrastructure would add complexity with no security benefit in this threat model. |
| **No containerization** | Docker on Pi 4 adds memory overhead and startup latency. systemd gives direct hardware access (ALSA, GPIO) and simpler debugging. |
| **Manual deploy** (`deploy.sh` + systemd) | One Pi, one store. CI/CD pipeline would be overengineering. The deploy script is idempotent with health checks and release stamping. |
| **No RBAC** | Single operator per store. Role separation adds UX friction for the target user (store staff, not IT teams). |

---

## License

Proprietary. All rights reserved. See `LICENSE`.
