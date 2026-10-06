#!/bin/bash
# ================================
# AnnounceFlow - Pull a field dump (read-only)
# ================================
#
# Copies what scripts/usage_report.py, diagnose.py and incident_report.py
# read: logs/ (events + ffmpeg receiver log), announceflow.log*,
# release_stamp.json and a consistent copy of announceflow.db.
# config.json and .env are NOT copied (they hold credentials).
#
# The only write on the device is a temporary SQLite backup in /tmp, which
# is removed afterwards (the live DB is in WAL mode; copying the file alone
# can miss recent writes).
#
# Usage:
#   scripts/pull_dump.sh <host> [dest-dir]
#   scripts/pull_dump.sh announceflow-osmaniye-gapgross ~/announceflow-dumps
# Then:
#   python3 scripts/usage_report.py --dir ~/announceflow-dumps/<host>-<date>

set -euo pipefail

if [ $# -lt 1 ]; then
    echo "Usage: $0 <host> [dest-dir]" >&2
    exit 1
fi

PI_USER="${PI_USER:-admin}"
HOST="$1"
DEST_ROOT="${2:-$HOME/announceflow-dumps}"
DEST="${DEST_ROOT}/${HOST}-$(date +%Y%m%d)"
REMOTE_DIR="/home/${PI_USER}/announceflow"
REMOTE_DB_COPY="/tmp/announceflow_dump_$$.db"
TARGET="${PI_USER}@${HOST}"

mkdir -p "${DEST}/logs"

echo "Pulling logs from ${TARGET} -> ${DEST}"
rsync -az "${TARGET}:${REMOTE_DIR}/logs/" "${DEST}/logs/"
rsync -az --include='announceflow.log*' --include='release_stamp.json' --exclude='*' \
    "${TARGET}:${REMOTE_DIR}/" "${DEST}/"

echo "Taking a consistent DB snapshot"
ssh "${TARGET}" "python3 -c \"
import sqlite3
src = sqlite3.connect('file:${REMOTE_DIR}/announceflow.db?mode=ro', uri=True)
dst = sqlite3.connect('${REMOTE_DB_COPY}')
src.backup(dst)
dst.close(); src.close()
\""
scp -q "${TARGET}:${REMOTE_DB_COPY}" "${DEST}/announceflow.db"
ssh "${TARGET}" "rm -f '${REMOTE_DB_COPY}'"

echo "Done: ${DEST} ($(du -sh "${DEST}" | cut -f1))"
