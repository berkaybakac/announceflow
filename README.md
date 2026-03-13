<div align="center">

# AnnounceFlow

**Production-deployed in-store audio control system**

[![Python](https://img.shields.io/badge/Python-3.9+-3776AB?logo=python&logoColor=white)](https://python.org)
[![Flask](https://img.shields.io/badge/Flask-3.0-000000?logo=flask)](https://flask.palletsprojects.com)
[![SQLite](https://img.shields.io/badge/SQLite-Database-003B57?logo=sqlite)](https://sqlite.org)
[![Raspberry Pi](https://img.shields.io/badge/Raspberry%20Pi%204-Deployed-A22846?logo=raspberrypi)](https://raspberrypi.org)

*Running in a real store environment since January 2026*

</div>

---

## Overview

AnnounceFlow is an in-store audio control platform built for single-branch operations on Raspberry Pi 4.
It handles background playlist playback, scheduled announcements, and live stream control from a Windows sender.
The web panel is focused on daily operators, while the Windows agent is focused on technical setup and fast actions.
The implementation is shaped by field constraints: LAN instability, power loss, and limited on-site technical support.
Deployment is intentionally simple: SSH + rsync + systemd with manual release control.

---

## Technical Stack

| Layer | Technology |
|-------|------------|
| Backend | Python 3.9+, Flask 3.0, Waitress WSGI |
| Database | SQLite with repository pattern |
| Frontend | HTML5, CSS3, vanilla JavaScript, Jinja2 |
| Audio Engine | mpg123, FFmpeg |
| Deployment | systemd, SSH, rsync |
| Hardware | Raspberry Pi 4 |

---

## Architecture

```text
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

REST API with Flask Blueprints and JSON payloads.

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/health` | Health check (no auth) |
| POST | `/api/play` | Play media file |
| POST | `/api/stop` | Stop playback |
| POST | `/api/volume` | Set volume (0-100) |
| GET | `/api/now-playing` | Current player state |
| GET | `/api/media/music` | List music files |
| POST | `/api/playlist/start-all` | Start playlist loop |
| POST | `/api/stream/start` | Start receiver-side stream session |
| POST | `/api/stream/stop` | Stop stream session |
| GET | `/api/stream/status` | Stream state and ownership |
| POST | `/api/stream/heartbeat` | Keep stream session alive |
| GET | `/downloads/agent/latest` | Download latest `StatekSound.exe` |

Authentication is session-based for protected endpoints.

---

## Key Features

### Playback and Scheduling

- Playlist playback with loop and recovery from interruptions.
- One-time and recurring announcement schedules.
- Business-hours policy with automatic mute behavior.
- Prayer-time integration via Turkish Diyanet API with local caching.

### Live Stream and Agent

- Stream start/stop/status lifecycle via dedicated endpoints.
- Sender heartbeat model to prevent stale stream sessions.
- Windows agent with host-first login and LAN fallback behavior.
- Web panel agent distribution (`Settings > Windows Agent > Agent İndir`).

### Operational Reliability

- State persistence in SQLite for restart safety.
- Manual, repeatable deployment with release metadata stamping.
- Built-in diagnostics for sender/receiver troubleshooting.

---

## Project Structure

```text
announceflow/
├── main.py                 # runtime entrypoint and startup wiring
├── web_panel.py            # Flask routes for pages and auth
├── player.py               # playback engine orchestration
├── scheduler.py            # schedule trigger loop
├── stream_manager.py       # stream lifecycle and ownership
├── _stream_receiver.py     # Pi-side ffmpeg receiver process
│
├── routes/
│   ├── player_routes.py
│   ├── playlist_routes.py
│   ├── media_routes.py
│   ├── stream_routes.py
│   └── settings_routes.py
│
├── services/               # policy and business-logic modules
├── database/               # schema and repositories
├── agent/                  # Windows desktop agent source
├── templates/              # Jinja templates
├── scripts/                # diagnostics and preflight scripts
├── tests/                  # pytest suite
└── docs/                   # local notes (not versioned in Git)
```

---

## Deployment and Ops

### Development

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python main.py
```

### Production (Raspberry Pi)

```bash
# Standard deploy (dev/test)
./deploy.sh stateksound.local

# Customer delivery deploy (clean content)
DEPLOY_PROFILE=clean-delivery ./deploy.sh stateksound.local
```

Deploy profiles:

- `standard`: routine deploy for development and regression validation.
- `clean-delivery`: handoff-only deploy. It clears existing `media/`, `logs/`, `runtime/`, and local `*.db` files on target before first customer upload.

### Hostname Standard (Single Branch = Single Pi)

- Primary endpoint for panel and agent: `http://stateksound.local:5001`
- Hostname-first is the default operational model.
- If hostname resolution fails on-site, use router/ARP-discovered Pi IP as temporary fallback.

### Windows Agent Distribution Flow

1. Put the latest EXE at `agent/releases/StatekSound.exe`.
2. After deployment, technical staff downloads EXE from panel (`/downloads/agent/latest`).
3. First Windows login should use `http://stateksound.local:5001`.

### Version Visibility in Settings

`Settings > Hakkında` reads version from `release_stamp.json` (`ref` field).
If release metadata is missing, UI shows `Sürüm bilinmiyor`.

### Release Workflow

1. Build/update `StatekSound.exe`.
2. Place EXE under `agent/releases/StatekSound.exe`.
3. Run standard deploy (`./deploy.sh stateksound.local`).
4. Run test gate (`python -m pytest -q`).
5. Validate panel health and agent download path.
6. Commit -> tag -> release notes.
7. Keep operational details in internal ops notes, not in release body.

### Customer Handoff Checklist

1. Run delivery deploy:
   `DEPLOY_PROFILE=clean-delivery ./deploy.sh stateksound.local`
2. Verify health:
   `curl -s http://stateksound.local:5001/api/health`
3. Verify panel opens and `Settings > Windows Agent` download works.
4. Confirm library has no legacy/test media content.
5. Upload first customer content from panel.

### Operational Roles

- **Technical staff**: network onboarding, first login, EXE setup, and fallback diagnostics.
- **Store operator**: daily playback/announcement usage from the web panel.
- **Developer/maintainer**: deploys releases, monitors regressions, and publishes release notes.

---

## Testing

```bash
python -m pytest -q
```

Test coverage includes API/auth flows, stream lifecycle, agent login/discovery behavior, settings UI states, and sender/receiver diagnostics.

---

## Environment Configuration

| Variable | Description |
|----------|-------------|
| `FLASK_SECRET_KEY` | Session security key |
| `ANNOUNCEFLOW_WEB_PORT` | Server port (default: 5001) |
| `ANNOUNCEFLOW_MEDIA_FOLDER` | Media storage path |

---

## What This Project Demonstrates

- Operating a production Flask service on constrained edge hardware.
- Designing LAN-first behavior with deterministic fallback strategy.
- Hardening stream lifecycle with ownership and heartbeat controls.
- Keeping operator UX simple while preserving technical support pathways.
- Running manual but traceable release/deploy workflows.

---

## Limitations

- Frontend uses vanilla JavaScript (no React/Vue framework layer).
- SQLite is used instead of PostgreSQL/MySQL.
- Authentication is session-based (no JWT/OAuth).
- No containerization (Docker/Kubernetes not used).
- Core deployment is manual (`deploy.sh` + systemd).
- CI automation is currently focused on Windows agent build/release workflows.
- Single-user system; no role-based access control.

---

## License

Proprietary. All rights reserved. See `LICENSE`.
Contribution policy: see `CONTRIBUTING.md`.
