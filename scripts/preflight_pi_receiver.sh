#!/usr/bin/env bash
set -u

ROOT_DIR="${1:-$(pwd)}"
REPORT_PATH="${2:-/tmp/announceflow_pi_preflight_$(date +%Y%m%d_%H%M%S).txt}"

PASS=0
WARN=0
FAIL=0

log() {
  printf '%s\n' "$*" | tee -a "$REPORT_PATH"
}

pass() {
  PASS=$((PASS + 1))
  log "[PASS] $1"
}

warn() {
  WARN=$((WARN + 1))
  log "[WARN] $1"
}

fail() {
  FAIL=$((FAIL + 1))
  log "[FAIL] $1"
}

mkdir -p "$(dirname "$REPORT_PATH")"
: > "$REPORT_PATH"

log "=== AnnounceFlow Pi Receiver Preflight ==="
log "generated_at=$(date -Iseconds)"
log "root_dir=$ROOT_DIR"
log "report=$REPORT_PATH"

if command -v ffmpeg >/dev/null 2>&1; then
  pass "ffmpeg found: $(command -v ffmpeg)"
else
  fail "ffmpeg not found in PATH"
fi

APLAY_OK=0
if command -v aplay >/dev/null 2>&1; then
  APLAY_OK=1
  pass "aplay found: $(command -v aplay)"
else
  fail "aplay not found (install alsa-utils)"
fi

if command -v python3 >/dev/null 2>&1; then
  pass "python3 found: $(command -v python3)"
elif command -v python >/dev/null 2>&1; then
  pass "python found: $(command -v python)"
else
  fail "python interpreter not found"
fi

if ffmpeg -hide_banner -formats 2>/dev/null | grep -E 'E\s+alsa' >/dev/null 2>&1; then
  pass "ffmpeg ALSA output format available"
else
  fail "ffmpeg ALSA output format not available"
fi

if [[ $APLAY_OK -eq 1 ]]; then
  ALSA_LIST="$(aplay -l 2>&1)"
  if echo "$ALSA_LIST" | grep -qi "no soundcards found"; then
    fail "ALSA reports no soundcards"
  else
    pass "ALSA soundcards detected"
  fi
  log "--- aplay -l ---"
  log "$ALSA_LIST"
else
  warn "Skipped aplay -l because aplay is missing"
fi

RECEIVER_SCRIPT="$ROOT_DIR/_stream_receiver.py"
if [[ -f "$RECEIVER_SCRIPT" ]]; then
  pass "receiver script found: $RECEIVER_SCRIPT"
else
  fail "receiver script not found: $RECEIVER_SCRIPT"
fi

if [[ $FAIL -eq 0 ]]; then
  PORT=$((5800 + RANDOM % 200))
  log "receiver_smoke_port=$PORT"
  python3 "$RECEIVER_SCRIPT" "$PORT" >/dev/null 2>&1 &
  PID=$!
  sleep 0.5
  if kill -0 "$PID" >/dev/null 2>&1; then
    pass "receiver process stayed alive for smoke window (pid=$PID)"
    kill "$PID" >/dev/null 2>&1 || true
    wait "$PID" >/dev/null 2>&1 || true
  else
    fail "receiver exited immediately (see stream_receiver_ffmpeg.log)"
    if [[ -f "$ROOT_DIR/logs/stream_receiver_ffmpeg.log" ]]; then
      log "--- tail logs/stream_receiver_ffmpeg.log ---"
      tail -n 50 "$ROOT_DIR/logs/stream_receiver_ffmpeg.log" | tee -a "$REPORT_PATH" >/dev/null
    else
      warn "stream_receiver_ffmpeg.log not found at $ROOT_DIR/logs"
    fi
  fi
fi

if [[ -n "${ANNOUNCEFLOW_ALSA_DEVICE:-}" ]]; then
  log "env ANNOUNCEFLOW_ALSA_DEVICE=$ANNOUNCEFLOW_ALSA_DEVICE"
fi
if [[ -n "${ANNOUNCEFLOW_ALSA_CARD:-}" ]]; then
  log "env ANNOUNCEFLOW_ALSA_CARD=$ANNOUNCEFLOW_ALSA_CARD"
fi

log "Summary: pass=$PASS warn=$WARN fail=$FAIL"
if [[ $FAIL -gt 0 ]]; then
  exit 2
fi
exit 0
