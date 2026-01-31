<div align="center">

# AnnounceFlow

### Intelligent Music & Announcement Management System for Commercial Spaces

**Set it once, let it run forever.**

[Features](#-features) · [Installation](#-installation) · [Quick Start](#-quick-start) · [FAQ](#-faq)

---

![Python](https://img.shields.io/badge/Python-3.9%2B-3776AB?style=for-the-badge&logo=python&logoColor=white)
![Flask](https://img.shields.io/badge/Flask-3.0-000000?style=for-the-badge&logo=flask&logoColor=white)
![Raspberry Pi](https://img.shields.io/badge/Raspberry%20Pi%204-A22846?style=for-the-badge&logo=raspberrypi&logoColor=white)
![SQLite](https://img.shields.io/badge/SQLite-003B57?style=for-the-badge&logo=sqlite&logoColor=white)
![Windows](https://img.shields.io/badge/Windows-Agent-0078D6?style=for-the-badge&logo=windows&logoColor=white)
![Version](https://img.shields.io/badge/Version-1.6.4-blue?style=for-the-badge)
![License](https://img.shields.io/badge/License-Proprietary-red?style=for-the-badge)

</div>

---

## Why AnnounceFlow?

As a restaurant, cafe, or retail store owner, you face the same problems every day:

| Problem | Result |
|---------|--------|
| Music wasn't turned on | Customers sit in awkward silence |
| Prayer time came but no one muted the music | Customer sensitivity issues |
| Business hours ended but music still playing | Unnecessary energy consumption |
| Staff changed | New employee doesn't know the system |
| Power outage | Everything resets to zero |

**AnnounceFlow** was built to solve exactly these problems. Running on a Raspberry Pi 4, this system operates **24/7 autonomously** once configured. No more waiting for staff to turn on music, tracking prayer times, or worrying about business hours.

> *"Configure once, forget forever. Let the system work for you."*

---

## Features

### Automated Music Management
- 24/7 uninterrupted background music
- Playlist support - play all songs in sequence and loop
- Supported formats: MP3, WAV, OGG, FLAC, M4A, WMA, AIFF
- Automatic format conversion (all formats converted to MP3)

### Prayer Time Integration
- Automatic prayer times via Turkish Diyanet API
- Precise timing for all 81 Turkish provinces and districts
- Auto-mute during prayer, resume from where it left off
- 7-day pre-cache for internet outage resilience

### Business Hours Automation
- Define opening and closing times
- Automatic silence outside business hours
- Auto-start when business opens
- Weekend/holiday support

### Scheduling System
- **One-time:** Schedule special announcements for specific date/time
- **Recurring:** Daily, hourly, or custom day announcements
- Announcements interrupt music, then music resumes from where it stopped

### Web-Based Management Panel
- Access from any device (phone, tablet, computer)
- Modern, user-friendly interface
- Secure access with username and password
- Real-time system status monitoring

### Windows Desktop Application
- System tray icon for quick access
- One-click music start/stop
- Volume control
- Easy EXE installation - no technical knowledge required

### Power Failure Recovery
- Resumes from last state after power outage
- Auto-start with systemd
- State persistence in database
- Zero intervention required

---

## Tech Stack

| Layer | Technology |
|-------|------------|
| **Backend** | Python 3.9+, Flask 3.0, Waitress WSGI |
| **Database** | SQLite (embedded, serverless) |
| **Audio Engine** | mpg123 (Pi), pygame (dev), ffmpeg |
| **Frontend** | HTML5, CSS3, JavaScript, Jinja2 |
| **Desktop** | tkinter, pystray, PyInstaller |
| **API** | Turkish Diyanet Prayer Times API |
| **Deployment** | systemd, GitHub Actions, rsync |
| **Hardware** | Raspberry Pi 4 (2GB+ RAM) |

---

## System Architecture

```
┌──────────────────┐      ┌──────────────────┐      ┌──────────────────┐
│  Windows Agent   │ ◄──► │   Flask Server   │ ◄──► │   Audio Player   │
│  (Desktop EXE)   │      │   (Port 5001)    │      │   (mpg123)       │
└──────────────────┘      └────────┬─────────┘      └──────────────────┘
                                   │
                    ┌──────────────┼──────────────┐
                    │              │              │
             ┌──────▼──────┐ ┌─────▼─────┐ ┌──────▼──────┐
             │  Scheduler  │ │  SQLite   │ │ Prayer API  │
             │  (Timing)   │ │  (Data)   │ │ (Diyanet)   │
             └─────────────┘ └───────────┘ └─────────────┘
```

---

## Installation

### Requirements

| Component | Requirement |
|-----------|-------------|
| **Hardware** | Raspberry Pi 4 (2GB+ RAM recommended) |
| **OS** | Raspberry Pi OS (64-bit Lite or Desktop) |
| **Audio Output** | 3.5mm jack, HDMI, or USB sound card |
| **Network** | Internet connection (for initial setup and prayer times) |

### Step 1: System Preparation

```bash
# Update system
sudo apt update && sudo apt upgrade -y

# Install required packages
sudo apt install -y python3 python3-pip python3-venv mpg123 ffmpeg git
```

### Step 2: Download Project

```bash
# Navigate to home directory
cd ~

# Clone the project
git clone https://github.com/USERNAME/announceflow.git
cd announceflow
```

### Step 3: Set Up Python Environment

```bash
# Create virtual environment (recommended)
python3 -m venv venv
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

### Step 4: Configure Audio Output

```bash
# List audio devices
aplay -l

# Configure for 3.5mm jack
sudo raspi-config
# Select: Advanced Options > Audio > Force 3.5mm jack
```

### Step 5: Install Service (Auto-Start)

```bash
# Copy service file
sudo cp announceflow.service /etc/systemd/system/

# Enable and start service
sudo systemctl daemon-reload
sudo systemctl enable announceflow
sudo systemctl start announceflow
```

### Step 6: Test Access

Open in your browser:
```
http://RASPBERRY_PI_IP:5001
```

**Default credentials:**
| Field | Value |
|-------|-------|
| Username | `admin` |
| Password | `admin123` |

> **Security:** Change your password on first login.

---

## Quick Start

### 1. Upload Music
- Go to **Library** tab in web panel
- Click **Upload Music** button
- Select MP3, WAV, FLAC, M4A files
- System automatically converts to MP3

### 2. Start Background Music
- On **Now Playing** page, click **Play All Music**
- System will play all songs in sequence and loop

### 3. Set Up Prayer Times
- Go to **Settings** > **Prayer Times**
- Select province and district
- Check "Mute during prayer times"
- System will automatically fetch times

### 4. Set Business Hours
- Go to **Settings** > **Business Hours**
- Opening time: `09:00`
- Closing time: `22:00`
- Check "Mute outside business hours"

### 5. Schedule Announcements (Optional)
- Go to **Schedules** > **One-time** or **Recurring**
- Select announcement file, date, and time
- Announcements interrupt music, then music resumes

---

## Real-World Impact

AnnounceFlow has been running flawlessly in a real fast-food restaurant for months.

### Before vs After

| Before | With AnnounceFlow |
|--------|-------------------|
| Staff forgets to turn on music daily | Automatic startup |
| Music plays during prayer time, complaints arise | Automatic muting |
| Music stays on at night, power waste | Automatic shutdown |
| Power outage resets everything | Automatic recovery |
| IT support calls for every issue | Zero maintenance |

### Savings

| Metric | Savings |
|--------|---------|
| **Staff time** | ~15 min/day x 365 days = **91 hours/year** |
| **Energy** | Night auto-off = **10-15% electricity savings** |
| **Customer satisfaction** | Prayer time sensitivity = **zero complaints** |
| **Maintenance cost** | Set-and-forget = **zero IT support** |

---

## Roadmap

### v1.7 - Multi-Device Management *(Planned)*
- [ ] Manage multiple Raspberry Pis from single panel
- [ ] Centralized music library synchronization
- [ ] Branch-based announcement management
- [ ] Group volume control

### v1.8 - Advanced Features
- [ ] Mobile app (iOS/Android)
- [ ] Cloud backup
- [ ] Detailed reporting and analytics

### v2.0 - Enterprise
- [ ] Multi-user roles
- [ ] API access
- [ ] Webhook integrations

---

## FAQ

<details>
<summary><strong>Do prayer times work during internet outage?</strong></summary>

Yes. The system pre-caches 7 days of prayer times. Even if internet is down for a week, the system continues to work correctly.
</details>

<details>
<summary><strong>What audio formats are supported?</strong></summary>

MP3, WAV, OGG, FLAC, M4A, WMA, AIFF formats are supported. Non-MP3 formats are automatically converted to MP3.
</details>

<details>
<summary><strong>Does it work with Raspberry Pi 3?</strong></summary>

It can work, but Raspberry Pi 4 is recommended. Performance issues may occur on Pi 3.
</details>

<details>
<summary><strong>Can I connect multiple speakers?</strong></summary>

Yes. Connect the 3.5mm output to an amplifier to distribute to multiple speakers. 100V line systems are recommended for professional installations.
</details>

<details>
<summary><strong>Can I use it without Windows Agent?</strong></summary>

Yes. Windows Agent is optional. All management can be done through the web panel. Agent just provides quick access convenience.
</details>

<details>
<summary><strong>How do I change the password?</strong></summary>

Go to **Settings** > **Security** in the web panel to change username and password.
</details>

---

## License

This software is licensed under **Proprietary License**.

- Commercial use requires permission
- Source code distribution is prohibited
- Modification rights belong to the owner

Contact for licensing and usage rights.

---

<div align="center">

**AnnounceFlow v1.6.4** · 2025

*Professional audio management for commercial spaces*

</div>
