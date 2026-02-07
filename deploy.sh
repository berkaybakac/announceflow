#!/bin/bash
# ================================
# AnnounceFlow - Deployment Script
# Raspberry Pi deployment
# ================================

set -e  # Exit on error

# Configuration
PI_USER="${PI_USER:-admin}"
# Use 1st argument as host if provided, else default
if [ -n "$1" ] && [ "$1" != "local" ]; then
    PI_HOST="$1"
else
    PI_HOST="${PI_HOST:-aflow.local}"
fi

DEST_DIR="/home/${PI_USER}/announceflow"
SERVICE_NAME="announceflow.service"
RELEASE_STAMP_FILE="release_stamp.json"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEPLOYED_AT_UTC="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"

if git -C "${SCRIPT_DIR}" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    RELEASE_COMMIT="$(git -C "${SCRIPT_DIR}" rev-parse HEAD 2>/dev/null || echo unknown)"
    RELEASE_COMMIT_SHORT="$(git -C "${SCRIPT_DIR}" rev-parse --short HEAD 2>/dev/null || echo unknown)"
    RELEASE_REF="$(git -C "${SCRIPT_DIR}" describe --tags --always --dirty 2>/dev/null || echo unknown)"
    RELEASE_BRANCH="$(git -C "${SCRIPT_DIR}" rev-parse --abbrev-ref HEAD 2>/dev/null || echo unknown)"
else
    RELEASE_COMMIT="unknown"
    RELEASE_COMMIT_SHORT="unknown"
    RELEASE_REF="unknown"
    RELEASE_BRANCH="unknown"
fi

RELEASE_STAMP_LOCAL="$(mktemp /tmp/announceflow_release_stamp_XXXXXX.json)"
cat > "${RELEASE_STAMP_LOCAL}" << EOF
{
  "commit": "${RELEASE_COMMIT}",
  "commit_short": "${RELEASE_COMMIT_SHORT}",
  "ref": "${RELEASE_REF}",
  "branch": "${RELEASE_BRANCH}",
  "deployed_at_utc": "${DEPLOYED_AT_UTC}"
}
EOF

# SSH Multiplexing to ask for password only once
SOCKET="/tmp/aflow_ssh_socket_$$"
SSH_OPTS="-4 -o StrictHostKeyChecking=accept-new -o ControlPath=$SOCKET -o ControlMaster=auto -o ControlPersist=600"

# Cleanup socket on exit
trap "rm -f $SOCKET $RELEASE_STAMP_LOCAL" EXIT

echo "========================================"
echo " AnnounceFlow - Deployment Script"
echo "========================================"
echo ""

# Check if we're deploying to Pi or running locally
if [ "$1" == "local" ]; then
    echo "Running locally..."
    python main.py
    exit 0
fi

echo "Deploying to Raspberry Pi: ${PI_HOST}"
echo ""

# 1. Create destination directory on Pi
echo "[1/5] Creating destination directory..."
ssh ${SSH_OPTS} ${PI_USER}@${PI_HOST} "mkdir -p ${DEST_DIR}"

# 1.5 Clean remote pycache to ensure fresh code
echo "[1.5/5] Cleaning remote pycache..."
ssh ${SSH_OPTS} ${PI_USER}@${PI_HOST} "find ${DEST_DIR} -name '__pycache__' -type d -exec rm -rf {} +"

# 2. Sync files
echo "[2/5] Syncing project files..."
rsync -avz --progress \
    -e "ssh ${SSH_OPTS}" \
    --exclude '__pycache__' \
    --exclude 'venv/' \
    --exclude '.venv/' \
    --exclude '.venv*/' \
    --exclude '*.pyc' \
    --exclude '.git' \
    --exclude '.DS_Store' \
    --exclude '.env' \
    --exclude 'config.json' \
    --exclude 'agent/build' \
    --exclude 'agent/dist' \
    --exclude 'logs/' \
    --exclude '*.db' \
    --exclude '*.log' \
    --exclude 'SUNUM_REHBERI.md' \
    ./ ${PI_USER}@${PI_HOST}:${DEST_DIR}/

echo ""
# 2.2 Upload release stamp (commit metadata)
echo "[2.2/5] Uploading release stamp..."
scp ${SSH_OPTS} "${RELEASE_STAMP_LOCAL}" ${PI_USER}@${PI_HOST}:${DEST_DIR}/${RELEASE_STAMP_FILE}
ssh ${SSH_OPTS} ${PI_USER}@${PI_HOST} "chmod 644 ${DEST_DIR}/${RELEASE_STAMP_FILE}"

echo ""
# Ensure Flask secret key exists on Pi and protect .env permissions
ssh ${SSH_OPTS} ${PI_USER}@${PI_HOST} "\
if [ ! -f ${DEST_DIR}/.env ]; then touch ${DEST_DIR}/.env; fi; \
if ! grep -q '^FLASK_SECRET_KEY=' ${DEST_DIR}/.env; then \
  echo \"FLASK_SECRET_KEY=\$(/usr/bin/python3 -c 'import secrets;print(secrets.token_hex(32))')\" >> ${DEST_DIR}/.env; \
fi; \
chmod 600 ${DEST_DIR}/.env"

echo ""
# 2.5 Install system dependencies (mpg123 needed for audio)
echo "[2.5/5] Installing system dependencies (mpg123, ffmpeg)..."
ssh ${SSH_OPTS} ${PI_USER}@${PI_HOST} "sudo apt-get update && sudo apt-get install -y mpg123 ffmpeg alsa-utils"

echo ""
echo "[3/5] Installing Python dependencies on Pi..."
# Filter out pygame and desktop build tools (pyinstaller, etc) from requirements.txt
ssh ${SSH_OPTS} ${PI_USER}@${PI_HOST} "cd ${DEST_DIR} && grep -vE 'pygame|pyinstaller|altgraph|macholib|pefile' requirements.txt > requirements_pi.txt && pip3 install --break-system-packages -r requirements_pi.txt"

echo ""
echo "[4/6] Creating systemd service file..."
cat > announceflow.service << EOF
[Unit]
Description=AnnounceFlow - Scheduled Audio System
After=network.target

[Service]
Type=simple
User=${PI_USER}
WorkingDirectory=${DEST_DIR}
ExecStart=/usr/bin/python3 ${DEST_DIR}/main.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF

# Copy service file
scp ${SSH_OPTS} announceflow.service ${PI_USER}@${PI_HOST}:${DEST_DIR}/

echo ""
echo "[5/6] Installing and reloading systemd service..."
ssh ${SSH_OPTS} ${PI_USER}@${PI_HOST} "sudo install -m 644 ${DEST_DIR}/${SERVICE_NAME} /etc/systemd/system/${SERVICE_NAME} && sudo systemctl daemon-reload && sudo systemctl enable ${SERVICE_NAME}"

echo ""
echo "[6/6] Restarting announceflow service..."
ssh ${SSH_OPTS} ${PI_USER}@${PI_HOST} "sudo systemctl restart ${SERVICE_NAME} && sudo systemctl status ${SERVICE_NAME} --no-pager | head -n 10"

echo ""
# 6.5 Post-deploy health check
echo "[6.5/6] Running post-deploy health check..."
ssh ${SSH_OPTS} ${PI_USER}@${PI_HOST} "cd ${DEST_DIR} && /usr/bin/python3 - << 'PY'
import json
import time
import urllib.request
from urllib.error import URLError, HTTPError

port = 5001
try:
    with open('config.json', 'r', encoding='utf-8') as f:
        raw = json.load(f).get('web_port', 5001)
    parsed = int(raw)
    if 1 <= parsed <= 65535:
        port = parsed
except Exception:
    pass

url = f'http://127.0.0.1:{port}/api/health'
last_error = 'unknown'
for attempt in range(1, 16):
    try:
        with urllib.request.urlopen(url, timeout=3) as response:
            body = response.read().decode('utf-8', errors='replace')
            data = json.loads(body)
            if response.status == 200 and data.get('status') == 'ok':
                print(f'Health check OK (attempt {attempt}): {url}')
                print(f'Backend: {data.get(\"player\", {}).get(\"backend\", \"unknown\")}')
                print(f'Scheduler running: {data.get(\"scheduler\", {}).get(\"running\", False)}')
                raise SystemExit(0)
            last_error = f'Unexpected payload/status: status={response.status}, body={body[:160]}'
    except (URLError, HTTPError, TimeoutError, json.JSONDecodeError) as exc:
        last_error = str(exc)
    time.sleep(2)

print(f'Health check FAILED after 15 attempts ({url}): {last_error}')
raise SystemExit(1)
PY"

echo ""
echo "========================================"
echo " Deployment & Restart Complete!"
echo "========================================"
echo ""
echo "Web panel: http://${PI_HOST}:5001"
echo "Release stamp: ref=${RELEASE_REF}, commit=${RELEASE_COMMIT_SHORT}, deployed_at=${DEPLOYED_AT_UTC}"
echo ""
echo "To view logs: tail -f ~/announceflow/announceflow.log"
