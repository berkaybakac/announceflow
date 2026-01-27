# AnnounceFlow

**Enterprise Scheduled Audio and Announcement System for Workplaces**

[![Python](https://img.shields.io/badge/Python-3.9+-3776AB?style=for-the-badge&logo=python&logoColor=white)](https://python.org)
[![Raspberry Pi](https://img.shields.io/badge/Raspberry_Pi-4-A22846?style=for-the-badge&logo=raspberrypi&logoColor=white)](https://raspberrypi.org)
[![mpg123](https://img.shields.io/badge/mpg123-Audio_Engine-00AA00?style=for-the-badge)](https://mpg123.de)

## About

AnnounceFlow is an IoT solution for automated music and announcement broadcasting in workplaces. It runs on Raspberry Pi 4 and is managed through a modern web interface.

**Target Industries:** Manufacturing plants, corporate offices, schools, shopping malls

## Features

| Feature | Description |
|---------|-------------|
| **Web Dashboard** | Dark theme, responsive, Turkish UI, mobile-friendly |
| **Scheduling** | One-time, recurring (weekly), interval modes |
| **Media Library** | Upload, categorize (Music/Announcement), delete via web |
| **Live Volume** | Logarithmic curve for natural audio control (ALSA) |
| **Desktop Agent** | Quick access via Tkinter GUI (PyInstaller EXE) |
| **IoT Ready** | Headless mode, systemd service, auto-start on boot |

## Technical Architecture

```
Browser ──HTTP/REST──▶ Flask (Waitress) ──▶ Scheduler ──▶ mpg123 ──▶ ALSA ──▶ 🔊
                              │
                           SQLite
```

| Layer | Technology |
|-------|------------|
| Backend | Python 3.9+, Flask 3.x, Waitress (16 threads) |
| Frontend | HTML5, CSS3 (Dark Theme), Vanilla JS |
| Database | SQLite3 |
| Audio | mpg123 → ALSA (card 2, PCM) with logarithmic volume |
| Deploy | systemd service, rsync over SSH |
| Hardware | Raspberry Pi 4 Model B (ARM64), USB/3.5mm audio |

## Project Structure

```
announceflow/
├── main.py              # Application entry point
├── web_panel.py         # Flask routes and API
├── player.py            # mpg123 + ALSA volume control
├── scheduler.py         # Background scheduling (30s interval)
├── database.py          # SQLite ORM
├── deploy.sh            # Pi deployment script
├── config.json          # Runtime config
├── requirements.txt     # Dependencies
├── templates/           # Jinja2 HTML (dark theme)
├── media/               # Audio storage (music/, announcements/)
└── agent/               # Desktop agent (Tkinter)
```

## Installation

### Local Development
```bash
git clone <repository_url>
cd announceflow
pip install -r requirements.txt
python main.py
# → http://localhost:5001 (admin / admin123)
```

### Production (Raspberry Pi)
```bash
./deploy.sh
# Auto: rsync files, install deps, configure systemd, start service
```

## Desktop Agent

Windows/macOS desktop app for quick access without browser.

**Features:** Login, file upload, volume control, stop playback, open web panel

**Build:**
```bash
cd agent
pip install pyinstaller
python build_agent.py
# Output: dist/AnnounceFlowAgent.exe (Win) or .app (macOS)
```

## API Reference

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/now-playing` | GET | Current playback state |
| `/api/play` | POST | Start playing (body: `{media_id}`) |
| `/api/stop` | POST | Stop playback |
| `/api/volume` | POST | Set volume 0-100 (body: `{volume}`) |
| `/api/media/upload` | POST | Upload audio file |
| `/api/media/<id>` | DELETE | Delete media |
| `/api/schedules/one-time` | GET/POST | One-time schedules |
| `/api/schedules/recurring` | GET/POST | Recurring schedules |

> **Note:** `/api/pause` and `/api/resume` are **deprecated** (mpg123 limitation)

## Configuration

**config.json:**
```json
{
    "volume": 80,
    "web_port": 5001,
    "scheduler_interval_seconds": 30
}
```

**deploy.sh:** Edit `PI_USER` and `PI_HOST` for your Pi connection.

## Troubleshooting

| Issue | Solution |
|-------|----------|
| No audio | `alsamixer -c 2` → unmute PCM, check USB audio |
| Service down | `sudo systemctl restart announceflow` |
| Web not loading | Check `journalctl -u announceflow -n 50` |
| SSH fails | `ssh -4 admin@aflow.local`, clear known_hosts |
| Low volume feels "dead" | Volume uses logarithmic curve, 50% ≈ 70% hardware |

## Version

**v1.3.1** · Logarithmic volume control · mpg123 audio engine

---
Proprietary. All rights reserved.
