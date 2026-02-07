# Pi Incident Analysis Runbook

This runbook implements the "last 3 hours freeze/latency investigation" flow for Raspberry Pi.

## 1. Collect evidence on Pi

Run on Raspberry Pi:

```bash
cd /home/admin/announceflow
chmod +x scripts/pi_incident_collect.sh scripts/pi_incident_report.py scripts/pi_incident_run.sh
./scripts/pi_incident_collect.sh
```

Optional flags:

```bash
./scripts/pi_incident_collect.sh --since "3 hours ago" --service announceflow --app-dir /home/admin/announceflow
```

The collector prints:

```text
COLLECTED_AT=/home/admin/pi_incident_YYYYmmdd_HHMMSS
```

## 2. Generate the report

```bash
python3 scripts/pi_incident_report.py --input-dir /home/admin/pi_incident_YYYYmmdd_HHMMSS
```

Or run the full flow in one command:

```bash
./scripts/pi_incident_run.sh
```

## 3. Output files

Inside the snapshot directory:

- `incident_report.md`: final incident report
- `keyword_hits.txt`: keyword-triggered lines from system logs
- `events_system.tsv`: `SYSTEM` shutdown/boot timeline
- `event_gap.txt`: max event-stream gap summary
- raw evidence logs (`journal_*.log`, `dmesg_tail.log`, `syslog_tail.log`, etc.)

## 4. Hourly automation (keep last 168 snapshots)

Install on Pi:

```bash
cd /home/admin/announceflow
chmod +x scripts/pi_incident_collect.sh scripts/pi_incident_report.py scripts/pi_incident_run.sh scripts/pi_incident_snapshot.sh

sudo cp systemd/pi-incident-snapshot.service /etc/systemd/system/
sudo cp systemd/pi-incident-snapshot.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now pi-incident-snapshot.timer
```

Verify:

```bash
systemctl status pi-incident-snapshot.timer --no-pager
systemctl list-timers --all | grep pi-incident-snapshot
```

Manual trigger test:

```bash
sudo systemctl start pi-incident-snapshot.service
sudo systemctl status pi-incident-snapshot.service --no-pager
ls -lah /home/admin/pi_incidents
```

Notes:

- Snapshot path pattern: `/home/admin/pi_incidents/pi_incident_YYYYmmdd_HHMMSS`
- Retention policy: keep latest `168` snapshots (hourly ~ last 7 days)
- Tune retention by editing `RETENTION_COUNT` in `systemd/pi-incident-snapshot.service`

## Notes

- The collector tolerates missing commands/files and logs warnings in `collection_warnings.log`.
- If sudo prompts for password, run as a user with sudo permission.
- For strongest root-cause confidence, collect snapshot immediately during or right after a freeze.
