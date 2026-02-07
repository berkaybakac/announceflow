#!/usr/bin/env bash
set -euo pipefail

# Hourly incident snapshot wrapper:
# 1) collect logs
# 2) generate report
# 3) keep only the latest N snapshots

APP_DIR="${APP_DIR:-/home/admin/announceflow}"
BASE_DIR="${BASE_DIR:-/home/admin/pi_incidents}"
RETENTION_COUNT="${RETENTION_COUNT:-24}"
SINCE_WINDOW="${SINCE_WINDOW:-3 hours ago}"
SERVICE_NAME="${SERVICE_NAME:-announceflow}"

ts="$(date +%Y%m%d_%H%M%S)"
out_dir="${BASE_DIR}/pi_incident_${ts}"

mkdir -p "${BASE_DIR}"

cd "${APP_DIR}"

./scripts/pi_incident_collect.sh \
  --since "${SINCE_WINDOW}" \
  --service "${SERVICE_NAME}" \
  --app-dir "${APP_DIR}" \
  --out-dir "${out_dir}" \
  --no-sudo

python3 ./scripts/pi_incident_report.py --input-dir "${out_dir}"

# Keep latest N snapshots only
mapfile -t snapshots < <(
  find "${BASE_DIR}" -maxdepth 1 -mindepth 1 -type d -name 'pi_incident_*' | sort
)

count="${#snapshots[@]}"
if (( count > RETENTION_COUNT )); then
  delete_count=$((count - RETENTION_COUNT))
  for ((i = 0; i < delete_count; i++)); do
    rm -rf "${snapshots[$i]}"
  done
fi

echo "SNAPSHOT_DONE=${out_dir}"
