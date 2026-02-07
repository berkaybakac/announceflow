#!/usr/bin/env bash
set -u

SINCE="3 hours ago"
SERVICE_NAME="announceflow"
APP_DIR="/home/admin/announceflow"
OUT_DIR="${HOME}/pi_incident_$(date +%Y%m%d_%H%M%S)"
USE_SUDO=1

usage() {
  cat <<'EOF'
Usage: pi_incident_collect.sh [options]

Collects the last N hours of incident evidence on Raspberry Pi.

Options:
  --since "<time expr>"    journalctl time window (default: "3 hours ago")
  --service <name>         systemd service name (default: announceflow)
  --app-dir <path>         app directory (default: /home/admin/announceflow)
  --out-dir <path>         output directory
  --no-sudo                do not prefix privileged commands with sudo
  -h, --help               show this help
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --since)
      SINCE="${2:-}"
      shift 2
      ;;
    --service)
      SERVICE_NAME="${2:-}"
      shift 2
      ;;
    --app-dir)
      APP_DIR="${2:-}"
      shift 2
      ;;
    --out-dir)
      OUT_DIR="${2:-}"
      shift 2
      ;;
    --no-sudo)
      USE_SUDO=0
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

mkdir -p "$OUT_DIR"
WARN_FILE="${OUT_DIR}/collection_warnings.log"
touch "$WARN_FILE"

log() {
  printf '%s\n' "$1"
}

warn() {
  printf '%s\n' "$1" | tee -a "$WARN_FILE" >&2
}

have_cmd() {
  command -v "$1" >/dev/null 2>&1
}

SUDO_CMD=()
if [[ "$USE_SUDO" -eq 1 ]] && have_cmd sudo; then
  SUDO_CMD=(sudo)
fi

run_capture() {
  local out_name="$1"
  shift
  local err_name="${out_name}.err"
  "$@" >"${OUT_DIR}/${out_name}" 2>"${OUT_DIR}/${err_name}"
  local exit_code=$?
  if [[ $exit_code -eq 0 ]]; then
    if [[ ! -s "${OUT_DIR}/${err_name}" ]]; then
      rm -f "${OUT_DIR:?}/${err_name}"
    fi
    log "OK  ${out_name}"
    return 0
  fi
  warn "WARN ${out_name} (exit=${exit_code}): $*"
  return 0
}

run_capture_sh() {
  local out_name="$1"
  shift
  local cmd="$*"
  local err_name="${out_name}.err"
  bash -lc "$cmd" >"${OUT_DIR}/${out_name}" 2>"${OUT_DIR}/${err_name}"
  local exit_code=$?
  if [[ $exit_code -eq 0 ]]; then
    if [[ ! -s "${OUT_DIR}/${err_name}" ]]; then
      rm -f "${OUT_DIR:?}/${err_name}"
    fi
    log "OK  ${out_name}"
    return 0
  fi
  warn "WARN ${out_name} (exit=${exit_code}): ${cmd}"
  return 0
}

log "Collecting incident evidence into: ${OUT_DIR}"

{
  echo "collected_at_utc=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "hostname=$(hostname 2>/dev/null || echo unknown)"
  echo "since=${SINCE}"
  echo "service_name=${SERVICE_NAME}"
  echo "app_dir=${APP_DIR}"
  echo "collector_user=$(whoami 2>/dev/null || echo unknown)"
  echo "kernel=$(uname -a 2>/dev/null || echo unknown)"
} > "${OUT_DIR}/collection_meta.env"

if have_cmd journalctl; then
  run_capture "journal_all.log" "${SUDO_CMD[@]}" journalctl --since "$SINCE" --no-pager
  run_capture "journal_${SERVICE_NAME}.log" "${SUDO_CMD[@]}" journalctl -u "$SERVICE_NAME" --since "$SINCE" --no-pager
  run_capture "journal_kernel.log" "${SUDO_CMD[@]}" journalctl -k --since "$SINCE" --no-pager
else
  warn "journalctl not found; journal logs skipped."
fi

if have_cmd dmesg; then
  run_capture_sh "dmesg_tail.log" "dmesg -T | tail -n 1200"
else
  warn "dmesg not found; kernel ring buffer skipped."
fi

if [[ -f /var/log/syslog ]]; then
  run_capture "syslog_tail.log" "${SUDO_CMD[@]}" tail -n 3000 /var/log/syslog
else
  warn "/var/log/syslog not found; syslog tail skipped."
fi

if have_cmd systemctl; then
  run_capture "systemctl_status.log" systemctl status "$SERVICE_NAME" --no-pager
  run_capture "systemctl_show.log" systemctl show "$SERVICE_NAME" -p ActiveState -p SubState -p ExecMainPID -p ExecMainStartTimestamp -p NRestarts -p Restart
else
  warn "systemctl not found; service status skipped."
fi

if have_cmd uptime; then run_capture "uptime.log" uptime; fi
if have_cmd free; then run_capture "free.log" free -h; else warn "free not found."; fi
if have_cmd top; then run_capture "top.log" top -b -n 1; else warn "top not found."; fi
if have_cmd vmstat; then run_capture "vmstat.log" vmstat 1 10; else warn "vmstat not found."; fi
if have_cmd iostat; then run_capture "iostat.log" iostat -xz 1 5; else warn "iostat not found."; fi

if have_cmd ip; then run_capture "ip_link.log" ip -s link; else warn "ip not found."; fi
if have_cmd ss; then run_capture "ss_tanp.log" ss -tanp; else warn "ss not found."; fi
if have_cmd iw; then run_capture "wlan_link.log" iw dev wlan0 link; else warn "iw not found."; fi

if [[ -f "${APP_DIR}/announceflow.log" ]]; then
  run_capture_sh "announceflow.log" "tail -n 4000 \"${APP_DIR}/announceflow.log\""
else
  warn "announceflow.log not found under ${APP_DIR}."
fi

if [[ -f "${APP_DIR}/logs/events.jsonl" ]]; then
  run_capture_sh "events.jsonl" "tail -n 8000 \"${APP_DIR}/logs/events.jsonl\""
else
  warn "events.jsonl not found under ${APP_DIR}/logs."
fi

if have_cmd jq && [[ -f "${OUT_DIR}/events.jsonl" ]]; then
  run_capture_sh "events_flat.tsv" "jq -r '[.ts,.cat,.event,(.data.signal // \"-\")] | @tsv' \"${OUT_DIR}/events.jsonl\""
else
  warn "jq not found or events.jsonl missing; events_flat.tsv skipped."
fi

log "COLLECTED_AT=${OUT_DIR}"
