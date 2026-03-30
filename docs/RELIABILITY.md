# Reliability Notes

This document captures lower-level implementation details behind the higher-level claims in the root README.

## Power Loss Recovery

- Playlist state is persisted to SQLite after every playlist operation.
- On startup, restore is gated by prayer-time and business-hours policy before playback resumes.
- If prayer data is unavailable and fail-safe mode is active, startup restore stays silent instead of resuming music.

## Scheduler Correctness

- Schedule gap timers use `time.monotonic()` so NTP corrections do not replay or skip announcements.
- Scheduled announcements are deduped by `source:id:minute` to prevent duplicate queueing from the scheduler tick.
- A minimum gap is enforced between back-to-back announcements to avoid audio collisions and repeated rapid-fire playback.

## Voice / Media Handling

- Voice files uploaded as `.mp3` can contain AAC audio in an MP3 container.
- The player detects codec mismatch with `ffprobe` and converts incompatible files to a real MP3 before playback.
- Failed conversions remove partial output immediately.

## Stream Reliability

- Live stream input uses a configurable UDP FIFO buffer; the default is `4194304` bytes.
- The receiver tracks ALSA xruns, UDP overruns, demux errors, and timeout conditions, then exposes alert summaries through `/api/stream/alerts`.
- Each local stream session carries a correlation identifier in the format `local-{pid}-{timestamp}` for debugging and log correlation.

## Pi Audio and Device Fallbacks

- Raspberry Pi analog volume is calibrated with a sqrt-based mapping from UI `0-100` into the usable hardware range.
- The implementation was tuned against a Pi 4 analog output curve where the perceived change near the top end is small.
- ALSA playback falls back through environment-configured devices, detected cards, and known safe defaults such as `plughw:2,0`, `plughw:0,0`, and `default`.

## Process and Storage Hygiene

- SQLite runs in WAL mode for safer concurrent access between panel, scheduler, and runtime services.
- App logs rotate at `500_000` bytes with 3 backups; stream logs use a separate rotating writer capped at `2 MB` with 5 backups.
- Before starting a new mpg123 process, orphaned playback processes are cleaned up; ffmpeg stderr is drained continuously to avoid pipe-buffer deadlock.
