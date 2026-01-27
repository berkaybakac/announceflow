#!/bin/bash
# ================================
# AnnounceFlow - Deployment Script
# Raspberry Pi deployment
# ================================

set -e  # Exit on error

# Configuration
PI_USER="admin"
PI_HOST="aflow.local"
DEST_DIR="/home/${PI_USER}/announceflow"
SERVICE_NAME="announceflow.service"

# SSH options: Force IPv4 to avoid mDNS IPv6 timeout issues
SSH_OPTS="-4 -o StrictHostKeyChecking=accept-new"

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

# 2. Sync files
echo "[2/5] Syncing project files..."
rsync -avz --progress \
    -e "ssh ${SSH_OPTS}" \
    --exclude '__pycache__' \
    --exclude 'venv' \
    --exclude '*.pyc' \
    --exclude '.git' \
    --exclude '.DS_Store' \
    --exclude 'agent/build' \
    --exclude 'agent/dist' \
    --exclude '*.db' \
    --exclude '*.log' \
    --exclude '.env' \
    --exclude 'SUNUM_REHBERI.md' \
    ./ ${PI_USER}@${PI_HOST}:${DEST_DIR}/

echo ""
echo "[3/5] Installing dependencies on Pi..."
ssh ${SSH_OPTS} ${PI_USER}@${PI_HOST} "cd ${DEST_DIR} && pip3 install --break-system-packages -r requirements.txt"

echo ""
echo "[4/5] Creating systemd service file..."
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
echo "[5/5] Setting up systemd service..."
ssh ${SSH_OPTS} ${PI_USER}@${PI_HOST} "sudo cp ${DEST_DIR}/announceflow.service /etc/systemd/system/ && sudo systemctl daemon-reload && sudo systemctl enable announceflow"

echo ""
echo "========================================"
echo " Deployment Complete!"
echo "========================================"
echo ""
echo "To start the service:"
echo "  ssh ${PI_USER}@${PI_HOST}"
echo "  sudo systemctl start announceflow"
echo ""
echo "Web panel will be available at:"
echo "  http://${PI_HOST}:5001"
echo ""
echo "To check status: sudo systemctl status announceflow"
echo "To view logs:    tail -f ~/announceflow/announceflow.log"
