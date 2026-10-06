# Deployment and Operations

## Development

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python main.py
```

## Production (Raspberry Pi)

```bash
# Standard deploy (dev/test)
./deploy.sh stateksound.local

# Field update deploy (preserve customer content)
DEPLOY_PROFILE=field-update ./deploy.sh stateksound.local

# Customer delivery deploy (clean content)
DEPLOY_PROFILE=clean-delivery ./deploy.sh stateksound.local
```

### Deploy Profiles

- `standard`: routine deploy for development and regression validation.
- `field-update`: live-site update. Preserves target `media/`, `logs/`,
  `runtime/`, `config.json`, `.env`, and local database files.
- `clean-delivery`: handoff-only deploy. Clears existing `media/`, `logs/`, `runtime/`, and local `*.db` files on target before first customer upload.

### Deploy Pipeline Details

The `deploy.sh` script handles end-to-end deployment:

1. Rsync project files to Pi (excludes `.git`, `venv`, `.env`, `config.json`, logs, databases).
2. (`field-update` only) Skip target customer content and runtime state during
   sync.
3. (clean-delivery only) Sanitize media/logs/runtime directories.
4. Upload release stamp (commit hash, ref, branch, UTC deploy timestamp).
5. Generate Flask secret key if missing, protect `.env` permissions.
6. Install system dependencies (`mpg123`, `ffmpeg`, `alsa-utils`); enable persistent
   journald capped at 200MB.
7. Install Python dependencies (filters out desktop-only packages for Pi).
8. Create and enable systemd service with auto-restart policy.
9. Post-deploy health check: retries `/api/health` up to 15 times (2s intervals), validates player backend and scheduler state.

SSH multiplexing is used to avoid repeated password prompts.

## Hostname Standard (Single Branch = Single Pi)

- Primary endpoint for panel and agent: `http://stateksound.local:5001`
- Hostname-first is the default operational model.
- If hostname resolution fails on-site, use router/ARP-discovered Pi IP as temporary fallback.

## Windows Agent Distribution Flow

1. Put the latest EXE at `agent/releases/StatekSound.exe`.
2. After deployment, technical staff downloads EXE from panel (`/downloads/agent/latest`).
3. First Windows login should use `http://stateksound.local:5001`.

## Admin Password Recovery

The web panel has a field emergency recovery flow for authorized support staff.
The recovery credential is distributed separately through private device notes,
not in this public repository.
Credential details are kept in the local-only, gitignored file:
`docs/DEVICE_NOTES.md`.

There is no visible "forgot password" button. Field recovery is handled through
the normal login form:

1. Open the web panel login page.
2. Enter the field recovery credential shared with authorized support staff.
3. The panel resets the admin credential to the emergency credential.
4. The panel redirects to the password change page.
5. Set a new customer password before using any protected panel page.

This is a LAN support shortcut, not a second factor. Keep remote access limited
to trusted networks or Tailscale, and change the password immediately after
recovery.

If the panel is unavailable, recover access from the server shell or SSH
account:

```bash
ssh admin@stateksound.local
cd /home/admin/announceflow
python3 scripts/reset_admin_password.py --username admin
sudo systemctl restart announceflow
```

For emergency field recovery to the original handoff credential:

```bash
python3 scripts/reset_admin_password.py --username admin
sudo systemctl restart announceflow
```

After login, change the password again from the Settings page. If `.env` or the
service environment defines `ANNOUNCEFLOW_ADMIN_USERNAME`,
`ANNOUNCEFLOW_ADMIN_PASSWORD`, `ADMIN_USERNAME`, or `ADMIN_PASSWORD`, update or
remove that override too; environment values replace `config.json` on restart.

## Release Workflow

1. Build/update `StatekSound.exe`.
2. Place EXE under `agent/releases/StatekSound.exe`.
3. Run standard deploy (`./deploy.sh stateksound.local`).
4. Run test gate (`python -m pytest -q`).
5. Validate panel health and agent download path.
6. Commit, tag, release notes.

## Analyzing a Pulled Field Dump

Pull logs and a consistent DB snapshot from a device (read-only; config.json
and .env are not copied):

```bash
scripts/pull_dump.sh <host> ~/announceflow-dumps
```

Usage report (active days, stream hours and quality, music, announcements,
panel logins) for one or more dumps; works on pre-v2.4.0 devices too:

```bash
python3 scripts/usage_report.py --dir ~/announceflow-dumps/<host>-<date> \
  [--dir ...] [--since 2026-07-01] [--json report.json]
```

Then, from inside the dump directory:

```bash
python3 /path/to/repo/diagnose.py 100000 --dir .          # health scoreboard
python3 /path/to/repo/scripts/incident_report.py \
  --around "2026-08-20T12:43:35" --window-minutes 15      # merged timeline
```

`incident_report.py` merges events.jsonl, announceflow.log, and
usage_sessions into one chronological view around a timestamp. Both tools
read rotated log backups (`.1`, `.2`, ...) automatically.

## XRUN Auto-Restart Validation (Staging/Pi)

### 288s Restart Isolation (Root Cause First)

Use this before any broad code change when you observe periodic receiver
restarts around ~288 seconds.

Goal:
- determine whether restarts are caused by stream logic, heartbeat/control path,
  xrun policy, or external/runtime process behavior.
- avoid large refactors until root cause is proven.

Minimum evidence set (same time window):
- `logs/events.jsonl` (Pi)
- `logs/stream_receiver_ffmpeg.log` (Pi)
- `%LOCALAPPDATA%\\AnnounceFlow\\logs\\agent_stream.log` + `stream_attempt_*.json` (Windows)

Run matrix (at least 20 minutes each):
1. Scenario A: stream only (no announcement, no policy boundary).
2. Scenario B: stream + announcement interruption.
3. Scenario C: stream + working-hours or prayer boundary.

Quick collection commands:

```bash
TS_UTC="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo "$TS_UTC"
```

```bash
python3 scripts/events_query.py \
  --file logs/events.jsonl \
  --since "$TS_UTC" \
  --summary

python3 scripts/stream_telemetry_report.py \
  --file logs/events.jsonl \
  --since "$TS_UTC" \
  --compact \
  --limit 400

rg -n "stream_receiver_summary|stream_receiver_exit_nonzero|stream_receiver_exit_controlled|stream_receiver_stop_reason|stream_heartbeat_expired|stream_desired_command_expired|stream_xrun_auto_restart|stream_takeover_start|stream_takeover_complete" logs/events.jsonl -S

rg -n "ALSA buffer xrun|Circular buffer overrun|Last message repeated|Exiting normally|Immediate exit requested" logs/stream_receiver_ffmpeg.log -S
```

```powershell
scripts\preflight_windows_audio.cmd
scripts\collect_windows_agent_logs.ps1 -LastMinutes 180
```

The bundle includes rotated agent logs and `power_events.csv` (last 7 days
of Windows sleep/wake events; `-PowerEventDays` to change). On the Pi side,
`stream_heartbeat_expired` (`elapsed_s`) and `stream_agent_heartbeat_returned`
(`offline_after_expiry_s`) show when the sender vanished and for how long.

Pattern triage:
1. `stream_xrun_auto_restart` near restart time:
   xrun policy is actively restarting.
2. `stream_heartbeat_expired` near restart time:
   heartbeat flow gap (agent/UI/network cadence).
3. `stream_desired_command_expired` or repeated desired-state updates:
   panel-agent command reconciliation issue.
4. Internal `stream_receiver_stop_reason` without explicit operator action:
   stream lifecycle path is triggering stop/start.
5. Nonzero ffmpeg exits without upstream stop reason:
   receiver/runtime/ALSA path issue.

Order of action:
1. prove root cause with matrix + synchronized logs.
2. if still ambiguous, add targeted telemetry (caller/context/correlation on stop/start).
3. apply one focused fix.
4. retest.

Expected event names:

- `stream_xrun_auto_restart_dry_run` (default mode, no real restart)
- `stream_xrun_auto_restart` (success only)
- `stream_xrun_auto_restart_aborted`
- `stream_xrun_auto_restart_skipped_cooldown`
- `stream_xrun_auto_restart_skipped_throttled`
- `stream_xrun_auto_restart_failed`
- `stream_sender_running_changed`

Expected payload keys (`stream_xrun_auto_restart*` events):

- `correlation_id`, `xruns_in_window`, `total_xruns`
- `restarts_this_hour`, `state`, `active`, `reason`
- `dry_run`, `threshold`, `window_seconds`
- `udp_overrun_total`, `xrun_status_age_seconds`
- `xrun_peak_1s`, `xrun_peak_60s`, `xrun_max_consecutive`, `xrun_current_consecutive`

XRUN runtime tuning (.env / environment):

- `ANNOUNCEFLOW_XRUN_AUTO_RECOVERY_DRY_RUN=true` (default)
- `ANNOUNCEFLOW_XRUN_RESTART_THRESHOLD=100` (default)
- `ANNOUNCEFLOW_XRUN_RESTART_WINDOW_SECONDS=300` (default)

Manual race scenario (critical):

1. Keep stream state `live`.
2. Increase `logs/receiver_xrun_status.json` `alsa_xrun` to cross threshold for active `correlation_id`.
3. Immediately trigger stream stop from panel/API.
4. Verify no false success:
   - terminal event should be `...aborted` or `...failed`
   - no `stream_xrun_auto_restart` for that intent
   - stream stays stopped (no unintended restart).

Manual cooldown scenario:

1. Trigger one successful auto-restart (`stream_xrun_auto_restart`).
2. Within 60 seconds, increase `alsa_xrun` above threshold again for the same active `correlation_id`.
3. Verify `stream_xrun_auto_restart_skipped_cooldown` is logged and no new restart occurs.
4. After 60 seconds, repeat threshold crossing and verify restart is allowed again.

Dry-run scenario (default safe mode):

1. Keep `ANNOUNCEFLOW_XRUN_AUTO_RECOVERY_DRY_RUN=true`.
2. Cross XRUN threshold for active `correlation_id`.
3. Verify `stream_xrun_auto_restart_dry_run` is logged.
4. Verify receiver process is not restarted (no stop/start cycle).

## Common XRUN Validation — Staged Priority/Buffer Test

Validates the fix for the *common* xrun pattern (moderate xrun count over long
sessions, `speed=` stays ~1.0000 — scheduling/jitter, not clock drift).
Does NOT apply to the rare throughput-drift sessions (huge burst, xrun starts
within ~1s of stream start, `speed` measurably <1.0 for the whole session) —
that stays a separate, open problem. See `docs/backlog.md` P0.

Candidates, in test order (cheapest/most-targeted first):

1. **Receiver niceness boost** (Pi-side, opt-in): `ANNOUNCEFLOW_STREAM_RECEIVER_NICE=-10`
   in `.env`. Requires `AmbientCapabilities=CAP_SYS_NICE` in the installed
   systemd unit — a normal `field-update` deploy does NOT reinstall the unit
   file by default; use `DEPLOY_INSTALL_SYSTEMD_SERVICE=1` (or `standard`
   profile) at least once for this to take effect.
2. **ALSA buffer/period widening** (Pi-side, no code change): via
   `ANNOUNCEFLOW_STREAM_FFMPEG_ARGS="-buffer_size 500000 -period_size 125000"`
   — test only if (1) alone isn't enough.

Stop as soon as a stage fails to reproduce the problem cleanly enough to
compare — don't jump straight to a long soak for every candidate.

### Stage 0 — Smoke (minutes, not hours)

1. Set `ANNOUNCEFLOW_STREAM_RECEIVER_NICE=-10`, restart service.
2. Confirm `stream_receiver_priority_applied` in `logs/events.jsonl` (not
   `..._failed` — if it fails, `AmbientCapabilities` likely isn't installed;
   fix the unit before continuing).
3. Confirm the service is otherwise healthy (`systemctl status`, one clean
   stream start/stop).

### Stage 1 — Latency check (single run, deterministic)

Measure `first_output_at - first_input_at` from `stream_receiver_summary`
(baseline vs candidate) and the existing announcement-interruption timing
check. This does not need repetition — it's not session-dependent noise.
Reject the candidate here if latency regresses noticeably; no need to spend
soak-test time on a candidate that already fails this.

### Stage 2 — Realistic few-hour comparison

Run each surviving candidate for a few hours under realistic load (continuous
stream + periodic announcement interruption + normal panel use), sequentially
on the same device (baseline → candidate A → candidate B). Pull comparable
windows with:

```bash
python3 diagnose.py --file logs/events.jsonl --minutes <window>
python3 scripts/stream_telemetry_report.py --file logs/events.jsonl --since <ts> --compact
```

Compare: xrun/hour, `SLOW_REQUEST` rate (web_panel — priority change must not
starve it), and flag/exclude any session matching the rare-drift signature
(see above) so it doesn't skew the comparison.

If a few hours already shows a clear, consistent improvement (or a clear
non-improvement), that's enough to decide — don't default to 24-48h.

### Stage 3 — Soak (only for the promising candidate)

Only if Stage 2 is ambiguous (small sample, inconsistent) or borderline: extend
that one candidate to a longer window (at least a full business day) before
committing. Don't soak-test a candidate that already failed Stage 1 or showed
no effect in Stage 2.

### Result (2026-09-08) — closed

Receiver niceness boost confirmed: Stage 0/1 passed, Stage 2 sessions with
the common (jitter, not drift) signature all landed well under the <5/hour
target once distinguished from the separate chronic/drift pattern (see
`docs/backlog.md`). Buffer/period widening was never needed.

**Rollout:** on by default (`_DEFAULT_RECEIVER_NICE = -10` in
`_stream_receiver.py`), documented in `.env.example`. Set
`ANNOUNCEFLOW_STREAM_RECEIVER_NICE=0` to opt out. No per-device `.env`
edit needed — this is the actual source of truth now.

## Chronic/Session-Specific XRUN — Next Occurrence Runbook

**Status:** open, root cause unknown. Separate from the common-xrun fix
above (that one's done). See `docs/backlog.md` for full investigation
history — this section is only "what to do when it happens again."

**How to recognize it (vs. normal/common xrun):**

```bash
grep -a -A2 "correlation_id=<the session>" logs/stream_receiver_ffmpeg.log | grep -E "ALSA buffer xrun|repeated"
```

If the first xrun appears within ~30s of session start AND xrun count in
`stream_receiver_summary` is more than a handful (dozens+) for that
session, it's this pattern, not a random blip.

**Data to pull the moment you notice it (or right after, while logs are
still fresh):**

1. `grep -a stream_sender_health logs/events.jsonl | grep <correlation_id>`
   — first ~60s now log every heartbeat (~4.5s resolution). Look at the
   CPU/mem/wifi trend right at session start.
2. Raw `logs/stream_receiver_ffmpeg.log` for that session — as of
   2026-09-10 ffmpeg no longer collapses repeats (`-loglevel repeat+info`),
   so every xrun has its own real timestamp. Check: constant low-rate drip,
   or a dense early burst, or something else. This is the one thing we
   couldn't see before and specifically added telemetry for.
3. `owner_device_id` / PC identity for that session (`stream_start_api_request`
   in events.jsonl) — confirm which physical PC, for pattern-matching
   against past occurrences.

**What's already ruled out (don't re-litigate without new evidence):**
sample-rate/clock drift as sole cause (speed= stays ~1x), fixed/broken
device (same PC produced both clean and chronic sessions), the existing
`stream_xrun_auto_restart` safety net (threshold=100/300s never trips for
this pattern — don't lower it blindly, within-burst density still unknown
until data from point 2 above accumulates).

**Record the findings back in `docs/backlog.md`'s chronic-xrun entry** —
current status, not a new dated diary entry.

