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

# SSH Multiplexing to ask for password only once
SOCKET="/tmp/aflow_ssh_socket_$$"
SSH_OPTS="-4 -o StrictHostKeyChecking=accept-new -o ControlPath=$SOCKET -o ControlMaster=auto -o ControlPersist=600"

# Cleanup socket on exit
trap "rm -f $SOCKET" EXIT

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
# If present, protect .env permissions on Pi
ssh ${SSH_OPTS} ${PI_USER}@${PI_HOST} "if [ -f ${DEST_DIR}/.env ]; then chmod 600 ${DEST_DIR}/.env; fi"

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
echo "========================================"
echo " Deployment & Restart Complete!"
echo "========================================"
echo ""
echo "Web panel: http://${PI_HOST}:5001"
echo ""
echo "To view logs: tail -f ~/announceflow/announceflow.log"
