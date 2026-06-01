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

# Customer delivery deploy (clean content)
DEPLOY_PROFILE=clean-delivery ./deploy.sh stateksound.local
```

### Deploy Profiles

- `standard`: routine deploy for development and regression validation.
- `clean-delivery`: handoff-only deploy. Clears existing `media/`, `logs/`, `runtime/`, and local `*.db` files on target before first customer upload.

### Deploy Pipeline Details

The `deploy.sh` script handles end-to-end deployment:

1. Rsync project files to Pi (excludes `.git`, `venv`, `.env`, `config.json`, logs, databases).
2. (clean-delivery only) Sanitize media/logs/runtime directories.
3. Upload release stamp (commit hash, ref, branch, UTC deploy timestamp).
4. Generate Flask secret key if missing, protect `.env` permissions.
5. Install system dependencies (`mpg123`, `ffmpeg`, `alsa-utils`).
6. Install Python dependencies (filters out desktop-only packages for Pi).
7. Create and enable systemd service with auto-restart policy.
8. Post-deploy health check: retries `/api/health` up to 15 times (2s intervals), validates player backend and scheduler state.

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

The field emergency credential is `admin` / `admin123`. It is intentionally
simple for customer handoff support: when the normal login fails but this
emergency credential is entered, the panel resets the admin credential to
`admin123`, logs the recovery event, and forces the user through the password
change page before any protected panel page can be used.

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
python3 scripts/reset_admin_password.py --username admin --password admin123
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
