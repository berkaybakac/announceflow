#!/usr/bin/env bash
set -euo pipefail

# AnnounceFlow - Raspberry Pi smoke check
# Usage:
#   PI_USER=admin ./scripts/pi_smoke_check.sh 192.168.1.24

PI_HOST="${1:-}"
PI_USER="${PI_USER:-admin}"
REMOTE_APP_DIR="${REMOTE_APP_DIR:-/home/${PI_USER}/announceflow}"

if [[ -z "${PI_HOST}" ]]; then
  echo "Usage: PI_USER=admin $0 <pi_ip_or_host>"
  exit 1
fi

ssh_pi() {
  ssh "${PI_USER}@${PI_HOST}" "$@"
}

section() {
  echo
  echo "== $1 =="
}

section "Service Status"
ssh_pi "systemctl is-active announceflow"
ssh_pi "sudo systemctl status announceflow --no-pager -l | head -n 20"

section "Health Endpoint"
HEALTH_JSON="$(ssh_pi "curl -fsS http://127.0.0.1:5001/api/health")"
echo "${HEALTH_JSON}"

if ! grep -q '"status":"ok"' <<<"${HEALTH_JSON}"; then
  echo "Health check failed: status != ok"
  exit 2
fi

section "Release Stamp"
ssh_pi "cat ${REMOTE_APP_DIR}/release_stamp.json"

section "One-time Schedule Status Summary"
ssh_pi "if command -v sqlite3 >/dev/null 2>&1; then sqlite3 ${REMOTE_APP_DIR}/announceflow.db \"SELECT status, COUNT(*) FROM one_time_schedules GROUP BY status ORDER BY status;\"; else python3 -c \"import sqlite3; conn=sqlite3.connect('${REMOTE_APP_DIR}/announceflow.db'); cur=conn.cursor(); rows=cur.execute('SELECT status, COUNT(*) FROM one_time_schedules GROUP BY status ORDER BY status').fetchall(); [print(f'{r[0]}|{r[1]}') for r in rows]; conn.close()\"; fi"

section "Playlist Intent Snapshot"
ssh_pi "if command -v sqlite3 >/dev/null 2>&1; then sqlite3 ${REMOTE_APP_DIR}/announceflow.db \"SELECT playlist_active, playlist_index, playlist_loop FROM playback_state WHERE id=1;\"; else python3 -c \"import sqlite3; conn=sqlite3.connect('${REMOTE_APP_DIR}/announceflow.db'); cur=conn.cursor(); rows=cur.execute('SELECT playlist_active, playlist_index, playlist_loop FROM playback_state WHERE id=1').fetchall(); [print(f'{r[0]}|{r[1]}|{r[2]}') for r in rows]; conn.close()\"; fi"

section "Recent Critical Logs (last 200 lines)"
ssh_pi "tail -n 200 ${REMOTE_APP_DIR}/announceflow.log | grep -E 'Prayer time - saving playlist state|Prayer ended - restoring playlist|plan_overlap_skip|resume_ok|resume_failed|working_hours_resume_ok|working_hours_resume_failed|playlist_track_|ERROR' || true"

section "Event Counters (last 500 events)"
ssh_pi "if [[ -f ${REMOTE_APP_DIR}/logs/events.jsonl ]]; then tail -n 500 ${REMOTE_APP_DIR}/logs/events.jsonl | grep -E 'plan_overlap_skip|resume_failed|working_hours_resume_failed|playlist_track_start_failed|playlist_track_missing' | wc -l | xargs echo 'warning_events='; else echo 'events.jsonl yok: event sayaci atlandi'; fi"

echo
echo "Smoke check completed."
