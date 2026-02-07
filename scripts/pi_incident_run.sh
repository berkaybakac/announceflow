#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

COLLECT_OUTPUT="$("${SCRIPT_DIR}/pi_incident_collect.sh" "$@")"
printf '%s\n' "${COLLECT_OUTPUT}"

OUT_DIR="$(printf '%s\n' "${COLLECT_OUTPUT}" | awk -F= '/^COLLECTED_AT=/{print $2}' | tail -n 1)"
if [[ -z "${OUT_DIR}" ]]; then
  echo "Could not resolve output directory from collector output." >&2
  exit 1
fi

python3 "${SCRIPT_DIR}/pi_incident_report.py" --input-dir "${OUT_DIR}"

echo "Done. Report: ${OUT_DIR}/incident_report.md"
