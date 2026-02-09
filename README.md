<div align="center">

# 🎵 AnnounceFlow

### Autonomous Audio Management System for Commercial Spaces

**Set it once. Let it run forever.**

[![Python](https://img.shields.io/badge/Python-3.9+-3776AB?style=for-the-badge&logo=python&logoColor=white)](https://python.org)
[![Flask](https://img.shields.io/badge/Flask-3.0-000000?style=for-the-badge&logo=flask&logoColor=white)](https://flask.palletsprojects.com)
[![SQLite](https://img.shields.io/badge/SQLite-Database-003B57?style=for-the-badge&logo=sqlite&logoColor=white)](https://sqlite.org)
[![Raspberry Pi](https://img.shields.io/badge/Raspberry%20Pi%204-Hardware-A22846?style=for-the-badge&logo=raspberrypi&logoColor=white)](https://raspberrypi.org)

[Features](#-key-features) · [Architecture](#-architecture) · [Tech Stack](#-tech-stack) · [Installation](#-installation) · [Screenshots](#-screenshots)

</div>

---

## 📋 Overview

**AnnounceFlow** is a production-ready, embedded audio management system designed for restaurants, cafes, and retail stores. Running on Raspberry Pi 4, it provides **24/7 autonomous operation** with zero daily maintenance.

The system was built to solve real-world problems faced by business owners:
- Staff forgetting to turn on background music
- Music playing during prayer times (culturally sensitive in Turkey)
- Audio continuing after business hours
- Complete reset after power outages

> 💡 **Real-World Deployment:** Successfully deployed and running in a fast-food restaurant since January 2026.

---

## ✨ Key Features

### 🎶 Intelligent Playback
- Continuous background music with playlist looping
- Multi-format support: MP3, WAV, FLAC, M4A, OGG, WMA, AIFF
- Automatic format conversion via FFmpeg
- Resume-from-position after interruptions

### 🕌 Prayer Time Integration
- Real-time prayer times via Turkish Diyanet API
- Coverage for all 81 Turkish provinces and 900+ districts
- 7-day cache for offline resilience
- Auto-mute during prayer, auto-resume after

### ⏰ Business Hours Automation
- Define opening/closing hours per day
- Automatic silence outside business hours
- Weekend and holiday support
- Zero staff intervention required

### 📢 Announcement Scheduling
- **One-time:** Schedule announcements for specific date/time
- **Recurring:** Daily, weekly, or custom patterns
- Priority interruption: Announcements pause music, then resume from exact position

### 🌐 Web-Based Control Panel
- Responsive design for mobile, tablet, and desktop
- Real-time system status monitoring
- Secure authentication with session management
- Dark mode UI

### 🖥️ Windows Desktop Agent
- System tray application for quick access
- One-click play/pause control
- Volume slider
- Built with PyInstaller for easy distribution

### ⚡ Power Failure Recovery
- Automatic service restart via systemd
- State persistence in SQLite database
- Resumes playback from last known position
- Zero-touch recovery

---

## 🏗️ Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                        Client Layer                              │
├──────────────────┬──────────────────┬───────────────────────────┤
│   Web Browser    │  Windows Agent   │     Mobile Browser        │
│   (Any Device)   │  (System Tray)   │    (Responsive UI)        │
└────────┬─────────┴────────┬─────────┴─────────────┬─────────────┘
         │                  │                       │
         └──────────────────┼───────────────────────┘
                            │ HTTP/REST API
                            ▼
┌─────────────────────────────────────────────────────────────────┐
│                    Flask Application Server                      │
│                      (Waitress WSGI)                             │
├─────────────────────────────────────────────────────────────────┤
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────────────────┐  │
│  │   Routes    │  │   Player    │  │      Scheduler          │  │
│  │ (Blueprints)│  │  (mpg123)   │  │ (APScheduler + Custom)  │  │
│  └─────────────┘  └─────────────┘  └─────────────────────────┘  │
├─────────────────────────────────────────────────────────────────┤
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────────────────┐  │
│  │  Services   │  │ Repositories│  │    Prayer Times API     │  │
│  │  (Logic)    │  │  (SQLite)   │  │    (Diyanet.gov.tr)     │  │
│  └─────────────┘  └─────────────┘  └─────────────────────────┘  │
└─────────────────────────────────────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────────────┐
│                      Hardware Layer                              │
├──────────────────┬──────────────────┬───────────────────────────┤
│  Raspberry Pi 4  │   Audio Output   │     Network/WiFi          │
│   (2GB+ RAM)     │  (3.5mm/HDMI)    │                           │
└──────────────────┴──────────────────┴───────────────────────────┘
```

---

## 🛠️ Tech Stack

| Layer | Technology | Purpose |
|-------|------------|---------|
| **Backend** | Python 3.9+, Flask 3.0 | Application server |
| **WSGI** | Waitress | Production-grade HTTP server |
| **Database** | SQLite | Embedded, serverless data persistence |
| **Audio** | mpg123, FFmpeg | Playback engine and format conversion |
| **Frontend** | HTML5, CSS3, JavaScript | Responsive web interface |
| **Templating** | Jinja2 | Server-side rendering |
| **Desktop** | Tkinter, Pystray, PyInstaller | Windows tray application |
| **Scheduling** | APScheduler + Custom Logic | Time-based automation |
| **External API** | Diyanet Prayer Times API | Islamic prayer time data |
| **Deployment** | systemd, rsync, SSH | Automated Pi deployment |
| **Hardware** | Raspberry Pi 4 | Embedded Linux platform |

---

## 📁 Project Structure

```
announceflow/
├── main.py                 # Application entry point
├── web_panel.py            # Flask app initialization
├── player.py               # Audio playback engine (mpg123/pygame)
├── scheduler.py            # Time-based job scheduling
├── prayer_times.py         # Diyanet API integration
├── logger.py               # Rotating file logger
│
├── routes/                 # Flask Blueprints (API endpoints)
│   ├── api_routes.py       # Core REST API
│   ├── playback_routes.py  # Play/pause/volume controls
│   ├── schedule_routes.py  # Announcement scheduling
│   └── settings_routes.py  # Configuration management
│
├── database/               # Data access layer
│   ├── __init__.py         # Schema initialization
│   ├── base_repository.py  # Abstract repository
│   ├── media_repository.py # Music file management
│   ├── playback_repository.py  # Playback state
│   └── schedule_repository.py  # Scheduled announcements
│
├── services/               # Business logic layer
├── templates/              # Jinja2 HTML templates
├── agent/                  # Windows desktop application
├── tests/                  # Unit and integration tests
│
├── deploy.sh               # One-command Pi deployment
├── requirements.txt        # Python dependencies
└── config.example.json     # Configuration template
```

---

## 🚀 Installation

### Prerequisites

| Component | Requirement |
|-----------|-------------|
| Hardware | Raspberry Pi 4 (2GB+ RAM recommended) |
| OS | Raspberry Pi OS (64-bit) |
| Audio | 3.5mm jack, HDMI, or USB sound card |
| Network | Internet for initial setup and prayer times |

### Quick Setup

```bash
# 1. System dependencies
sudo apt update && sudo apt install -y python3 python3-pip python3-venv mpg123 ffmpeg git

# 2. Clone repository
git clone https://github.com/YOUR_USERNAME/announceflow.git
cd announceflow

# 3. Python environment
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# 4. Configuration
cp config.example.json config.json
cp .env.example .env
# Edit config.json with your settings

# 5. Run directly (development)
python main.py

# 6. Install as service (production)
sudo cp announceflow.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable announceflow
sudo systemctl start announceflow
```

### One-Command Deployment

For remote deployment to Raspberry Pi:

```bash
./deploy.sh pi4.local
```

This script handles:
- File synchronization via rsync
- Python dependency installation
- systemd service configuration
- Automatic service restart
- Health check verification

---

## 📸 Screenshots

> *Screenshots coming soon*

---

## 🧪 Testing

```bash
# Run all tests
python -m pytest tests/ -v

# Run specific test file
python -m pytest tests/test_api.py -v
```

---

## 📈 Performance

Tested on Raspberry Pi 4 (2GB RAM):

| Metric | Value |
|--------|-------|
| Idle RAM usage | ~150MB |
| CPU load (playback) | <5% |
| Boot to playback | <30 seconds |
| Config read latency | <1ms (cached) |

---

## 🔒 Security Features

- Session-based authentication
- Environment-based secret key management
- XSS prevention with HTML escaping
- Rate limiting on sensitive endpoints
- File upload validation and sanitization

---

## 📄 License

This project is proprietary software. All rights reserved.

For licensing inquiries, please contact the repository owner.

---

<div align="center">

*Deployed and running in production since January 2026*

</div>
