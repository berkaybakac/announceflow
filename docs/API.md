# API Reference

REST API with Flask Blueprints, JSON payloads, and session-based authentication.

---

## Core

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/health` | Health check (no auth) |
| GET | `/api/now-playing` | Current player state |

## Playback

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/play` | Play media file |
| POST | `/api/stop` | Stop playback |
| POST | `/api/stop-preview` | Stop preview without breaking playlist loop |
| POST | `/api/volume` | Set volume (0–100) or toggle mute |
| POST | `/api/playlist/start-all` | Start playlist loop |

## Scheduling

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/schedules/one-time` | Create one-time schedule |
| POST | `/api/schedules/recurring` | Create recurring schedule |
| GET | `/api/schedules/day-slots` | Occupied slots for a date (timeline) |
| GET | `/api/schedules/week-slots` | Occupied slots for a full week |

## Stream

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/stream/start` | Start receiver-side stream session |
| POST | `/api/stream/stop` | Stop stream session |
| GET | `/api/stream/status` | Stream state and ownership |
| POST | `/api/stream/heartbeat` | Keep stream session alive |
| GET | `/api/stream/alerts` | Audio alert evaluation (ALSA/UDP) |

## Media and Distribution

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/media/music` | List music files |
| POST | `/api/media/upload` | Upload media file |
| GET | `/downloads/agent/latest` | Download latest `StatekSound.exe` |
