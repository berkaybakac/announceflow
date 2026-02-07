#!/usr/bin/env python3
"""
Generate an incident report from a collected Pi snapshot directory.
"""
from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable, List, Optional, Sequence, Tuple


SEVERITY_ORDER = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3}


@dataclass
class Finding:
    severity: str
    timestamp: str
    source: str
    evidence: str
    impact: str
    confidence: str


@dataclass
class EventRecord:
    ts_raw: str
    ts: Optional[datetime]
    cat: str
    event: str
    data: dict


@dataclass
class KeywordRule:
    severity: str
    pattern: re.Pattern[str]
    impact: str


KEYWORD_RULES: Sequence[KeywordRule] = (
    KeywordRule(
        "CRITICAL",
        re.compile(r"\boom\b|out of memory|killed process", re.IGNORECASE),
        "Memory pressure or OOM can freeze requests and kill critical processes.",
    ),
    KeywordRule(
        "CRITICAL",
        re.compile(r"soft lockup|hard lockup|kernel panic|panic", re.IGNORECASE),
        "Kernel lockup/panic indicates severe system instability.",
    ),
    KeywordRule(
        "CRITICAL",
        re.compile(r"i/o error|ext4|mmc", re.IGNORECASE),
        "Storage I/O issues can stall the whole system and web responses.",
    ),
    KeywordRule(
        "CRITICAL",
        re.compile(r"under.?voltage|throttl", re.IGNORECASE),
        "Power/thermal throttling can cause major latency and intermittent freezes.",
    ),
    KeywordRule(
        "HIGH",
        re.compile(r"watchdog|segfault", re.IGNORECASE),
        "Process crash/recovery loops can make the panel intermittently unavailable.",
    ),
    KeywordRule(
        "HIGH",
        re.compile(r"deauth|disconnect|link down|dhcp", re.IGNORECASE),
        "Network instability can cause delayed or failed panel connections.",
    ),
    KeywordRule(
        "MEDIUM",
        re.compile(r"timeout|timed out|unreachable|refused", re.IGNORECASE),
        "Timeout-level errors may indicate transient load, network, or dependency issues.",
    ),
)


TS_PATTERNS = (
    re.compile(r"\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}"),
    re.compile(r"[A-Z][a-z]{2}\s+\d{1,2}\s\d{2}:\d{2}:\d{2}"),
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Analyze collected Pi incident logs and generate a Markdown report."
    )
    parser.add_argument(
        "--input-dir",
        default=".",
        help="Snapshot directory produced by pi_incident_collect.sh (default: .)",
    )
    parser.add_argument(
        "--report-file",
        default=None,
        help="Output report file path (default: <input-dir>/incident_report.md)",
    )
    parser.add_argument(
        "--max-findings",
        type=int,
        default=25,
        help="Maximum suspicious findings rows in the report table (default: 25)",
    )
    return parser.parse_args()


def read_lines(path: Path) -> List[str]:
    try:
        return path.read_text(encoding="utf-8", errors="replace").splitlines()
    except FileNotFoundError:
        return []


def shorten(text: str, limit: int = 180) -> str:
    text = " ".join(text.strip().split())
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


def extract_timestamp(line: str) -> str:
    for pattern in TS_PATTERNS:
        match = pattern.search(line)
        if match:
            return match.group(0)
    return "-"


def safe_parse_dt(value: str) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        pass
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
    return None


def load_events(events_path: Path) -> List[EventRecord]:
    records: List[EventRecord] = []
    for line in read_lines(events_path):
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        ts_raw = str(obj.get("ts", ""))
        records.append(
            EventRecord(
                ts_raw=ts_raw,
                ts=safe_parse_dt(ts_raw),
                cat=str(obj.get("cat", "")),
                event=str(obj.get("event", "")),
                data=obj.get("data", {}) if isinstance(obj.get("data", {}), dict) else {},
            )
        )
    records.sort(key=lambda r: (r.ts_raw, r.cat, r.event))
    return records


def parse_app_log_stats(
    app_log_path: Path,
    window_start: Optional[datetime] = None,
    window_end: Optional[datetime] = None,
) -> Tuple[int, int, int, Optional[str], Optional[str]]:
    errors = 0
    warnings = 0
    slow_requests = 0
    first_ts: Optional[str] = None
    last_ts: Optional[str] = None

    for line in read_lines(app_log_path):
        line_dt: Optional[datetime] = None
        ts: Optional[str] = None
        if re.match(r"^\d{4}-\d{2}-\d{2} ", line):
            ts = line[:19]
            line_dt = safe_parse_dt(ts)
        in_window = True
        if line_dt and window_start and line_dt < window_start:
            in_window = False
        if line_dt and window_end and line_dt > window_end:
            in_window = False
        if not in_window:
            continue
        if ts is not None:
            if first_ts is None:
                first_ts = ts
            last_ts = ts
        if " - ERROR - " in line:
            errors += 1
        if " - WARNING - " in line:
            warnings += 1
        if "SLOW_REQUEST" in line:
            slow_requests += 1
    return errors, warnings, slow_requests, first_ts, last_ts


def parse_systemctl_show(path: Path) -> dict:
    values = {}
    for line in read_lines(path):
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip()
    return values


def compute_downtimes(system_events: Sequence[EventRecord]) -> List[Tuple[EventRecord, EventRecord, int]]:
    downtimes: List[Tuple[EventRecord, EventRecord, int]] = []
    shutdown_queue: List[EventRecord] = []
    for rec in sorted(system_events, key=lambda x: x.ts_raw):
        if rec.event == "shutdown":
            shutdown_queue.append(rec)
            continue
        if rec.event != "boot" or not shutdown_queue:
            continue
        shutdown_event = shutdown_queue.pop(0)
        if shutdown_event.ts and rec.ts:
            seconds = int((rec.ts - shutdown_event.ts).total_seconds())
        else:
            seconds = -1
        downtimes.append((shutdown_event, rec, seconds))
    return downtimes


def compute_max_gap(events: Sequence[EventRecord]) -> Tuple[int, Optional[EventRecord], Optional[EventRecord]]:
    sorted_events = sorted([e for e in events if e.ts is not None], key=lambda x: x.ts)  # type: ignore[arg-type]
    if len(sorted_events) < 2:
        return 0, None, None
    max_gap = 0
    gap_from: Optional[EventRecord] = None
    gap_to: Optional[EventRecord] = None
    for first, second in zip(sorted_events, sorted_events[1:]):
        if first.ts is None or second.ts is None:
            continue
        seconds = int((second.ts - first.ts).total_seconds())
        if seconds > max_gap:
            max_gap = seconds
            gap_from = first
            gap_to = second
    return max_gap, gap_from, gap_to


def gap_within_known_downtime(
    gap_from: EventRecord, gap_to: EventRecord, downtimes: Sequence[Tuple[EventRecord, EventRecord, int]]
) -> bool:
    if gap_from.ts is None or gap_to.ts is None:
        return False
    for shutdown_event, boot_event, _ in downtimes:
        if shutdown_event.ts is None or boot_event.ts is None:
            continue
        if shutdown_event.ts <= gap_from.ts and gap_to.ts <= boot_event.ts:
            return True
    return False


def scan_keyword_matches(files: Iterable[Path]) -> Tuple[List[Finding], List[str]]:
    findings: List[Finding] = []
    raw_hits: List[str] = []

    for path in files:
        if not path.exists():
            continue
        for line_no, line in enumerate(read_lines(path), start=1):
            for rule in KEYWORD_RULES:
                if not rule.pattern.search(line):
                    continue
                ts = extract_timestamp(line)
                evidence = shorten(line)
                findings.append(
                    Finding(
                        severity=rule.severity,
                        timestamp=ts,
                        source=path.name,
                        evidence=evidence,
                        impact=rule.impact,
                        confidence="High" if rule.severity in {"CRITICAL", "HIGH"} else "Medium",
                    )
                )
                raw_hits.append(f"{path.name}:{line_no}:{line}")
                break
    return findings, raw_hits


def format_seconds(seconds: int) -> str:
    if seconds < 0:
        return "unknown"
    minutes, rem = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours > 0:
        return f"{hours}h {minutes}m {rem}s"
    if minutes > 0:
        return f"{minutes}m {rem}s"
    return f"{rem}s"


def dedupe_findings(findings: Sequence[Finding], max_findings: int) -> List[Finding]:
    seen = set()
    result: List[Finding] = []
    ordered = sorted(
        findings,
        key=lambda f: (
            SEVERITY_ORDER.get(f.severity, 99),
            f.timestamp if f.timestamp != "-" else "9999",
            f.source,
            f.evidence,
        ),
    )
    for finding in ordered:
        key = (finding.severity, finding.timestamp, finding.source, finding.evidence)
        if key in seen:
            continue
        seen.add(key)
        result.append(finding)
        if len(result) >= max_findings:
            break
    return result


def write_text(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")


def build_hypotheses(
    system_events: Sequence[EventRecord],
    keyword_hits: Sequence[str],
    prayer_cache_hits: int,
    total_events: int,
) -> List[str]:
    hypotheses: List[str] = []

    has_sigint_shutdown = any(
        rec.event == "shutdown" and str(rec.data.get("signal", "")).upper() == "SIGINT"
        for rec in system_events
    )
    if has_sigint_shutdown:
        hypotheses.append(
            "SIGINT kaynakli servis durdurmalari erisimi kesmis olabilir. "
            "Kanit: SYSTEM shutdown olaylarinda signal=SIGINT gorunuyor. "
            "Eksik kanit: Bu sinyali hangi prosesin gonderdigi OS audit ile teyit edilmeli."
        )

    hits_blob = "\n".join(keyword_hits).lower()
    if any(word in hits_blob for word in ("under-voltage", "undervoltage", "throttl")):
        hypotheses.append(
            "Guc veya termal throttling performans dalgalanmasina neden olmus olabilir. "
            "Karsit kanit: Bu snapshotta throttling hit'i yoksa hipotez zayif kalir."
        )
    elif any(word in hits_blob for word in ("oom", "out of memory", "killed process")):
        hypotheses.append(
            "Bellek baskisi (OOM) web panelin dakikalarca cevap verememesine yol acmis olabilir. "
            "Eksik kanit: OOM aninda process RSS ve swap degerleri gerekli."
        )
    elif any(word in hits_blob for word in ("i/o error", "ext4", "mmc")):
        hypotheses.append(
            "SD kart veya dosya sistemi I/O hatalari sistem takilmasi yaratmis olabilir. "
            "Eksik kanit: mmc/ext4 hata ornegi ve SMART benzeri saglik verisi gerekir."
        )

    if total_events > 0 and prayer_cache_hits / total_events >= 0.5:
        hypotheses.append(
            "Yuksek frekansli PRAYER cache_hit log yazimi I/O yukunu arttirarak gecikmeyi buyutmus olabilir. "
            "Karsit kanit: Tek basina bu patern genelde dakika seviyesinde donma uretmez."
        )

    if not hypotheses:
        hypotheses.append(
            "Mevcut kanitlarla tek bir kok neden kanitlanamadi. "
            "OS-level journal/dmesg bulgulari olmadan sonuc guveni dusuk kalir."
        )

    return hypotheses[:3]


def main() -> int:
    args = parse_args()
    input_dir = Path(args.input_dir).resolve()
    report_path = Path(args.report_file).resolve() if args.report_file else input_dir / "incident_report.md"

    events_path = input_dir / "events.jsonl"
    app_log_path = input_dir / "announceflow.log"
    if not events_path.exists() and (Path("logs/events.jsonl")).exists():
        events_path = Path("logs/events.jsonl").resolve()
    if not app_log_path.exists() and Path("announceflow.log").exists():
        app_log_path = Path("announceflow.log").resolve()

    events = load_events(events_path)
    event_counter = Counter((e.cat, e.event) for e in events)
    system_events = [e for e in events if e.cat == "SYSTEM" and e.event in {"shutdown", "boot"}]
    downtimes = compute_downtimes(system_events)
    restart_count = len(downtimes)
    max_downtime = max((seconds for _, _, seconds in downtimes if seconds >= 0), default=0)

    max_gap_sec, max_gap_from, max_gap_to = compute_max_gap(events)

    event_dt_values = [e.ts for e in events if e.ts is not None]
    event_window_start = min(event_dt_values) if event_dt_values else None
    event_window_end = max(event_dt_values) if event_dt_values else None

    app_errors, app_warnings, slow_requests, app_first_ts, app_last_ts = parse_app_log_stats(
        app_log_path,
        window_start=event_window_start,
        window_end=event_window_end,
    )
    systemctl_show = parse_systemctl_show(input_dir / "systemctl_show.log")
    service_nrestarts = int(systemctl_show.get("NRestarts", "0") or "0")

    scan_targets = [
        input_dir / "journal_all.log",
        input_dir / "journal_kernel.log",
        input_dir / "dmesg_tail.log",
        input_dir / "syslog_tail.log",
        input_dir / "journal_announceflow.log",
    ]
    scan_targets.extend(sorted(input_dir.glob("journal_*.log")))
    keyword_findings, keyword_hits = scan_keyword_matches(scan_targets)

    findings: List[Finding] = list(keyword_findings)

    for shutdown_event, boot_event, seconds in downtimes:
        severity = "HIGH" if seconds >= 30 else "MEDIUM"
        findings.append(
            Finding(
                severity=severity,
                timestamp=shutdown_event.ts_raw or "-",
                source="events.jsonl",
                evidence=(
                    f"shutdown(signal={shutdown_event.data.get('signal', '-')}) -> "
                    f"boot in {format_seconds(seconds)}"
                ),
                impact="Service downtime causes panel access interruptions.",
                confidence="High",
            )
        )

    if max_gap_sec >= 30 and max_gap_from and max_gap_to:
        is_shutdown_boot_gap = (
            max_gap_from.cat == "SYSTEM"
            and max_gap_from.event == "shutdown"
            and max_gap_to.cat == "SYSTEM"
            and max_gap_to.event == "boot"
        )
        in_known_downtime = gap_within_known_downtime(max_gap_from, max_gap_to, downtimes)
        if not is_shutdown_boot_gap and not in_known_downtime:
            findings.append(
                Finding(
                    severity="HIGH",
                    timestamp=max_gap_from.ts_raw,
                    source="events.jsonl",
                    evidence=(
                        f"Event stream gap {format_seconds(max_gap_sec)} between "
                        f"{max_gap_from.cat}/{max_gap_from.event} and {max_gap_to.cat}/{max_gap_to.event}"
                    ),
                    impact="Possible scheduler stall or blocked main flow.",
                    confidence="Medium",
                )
            )

    prayer_cache_hits = event_counter.get(("PRAYER", "cache_hit"), 0)
    total_events = len(events)
    if total_events > 0 and prayer_cache_hits >= 300 and (prayer_cache_hits / total_events) >= 0.5:
        findings.append(
            Finding(
                severity="MEDIUM",
                timestamp=events[0].ts_raw if events else "-",
                source="events.jsonl",
                evidence=(
                    f"High-frequency PRAYER/cache_hit: {prayer_cache_hits} of {total_events} events "
                    f"({(prayer_cache_hits / total_events) * 100:.1f}%)"
                ),
                impact="Frequent log writes can increase I/O pressure on SD storage.",
                confidence="Medium",
            )
        )

    if app_errors > 0:
        findings.append(
            Finding(
                severity="HIGH",
                timestamp=app_first_ts or "-",
                source=app_log_path.name,
                evidence=f"Application ERROR lines: {app_errors}",
                impact="Unhandled errors can block or degrade web operations.",
                confidence="Medium",
            )
        )
    if slow_requests > 0:
        findings.append(
            Finding(
                severity="MEDIUM",
                timestamp=app_first_ts or "-",
                source=app_log_path.name,
                evidence=f"SLOW_REQUEST warnings: {slow_requests}",
                impact="Slow endpoints directly match delayed panel interactions.",
                confidence="Medium",
            )
        )
    if service_nrestarts > 0:
        findings.append(
            Finding(
                severity="HIGH",
                timestamp="-",
                source="systemctl_show.log",
                evidence=f"NRestarts={service_nrestarts}",
                impact="Service restart loops can cause recurrent downtime.",
                confidence="High",
            )
        )

    if not findings:
        findings.append(
            Finding(
                severity="LOW",
                timestamp="-",
                source="analysis",
                evidence="No suspicious signature matched configured detection rules.",
                impact="No direct anomaly seen in available logs.",
                confidence="Low",
            )
        )

    selected_findings = dedupe_findings(findings, args.max_findings)

    event_start = events[0].ts_raw if events else app_first_ts or "-"
    event_end = events[-1].ts_raw if events else app_last_ts or "-"

    findings_rows = "\n".join(
        f"| {f.severity} | {f.timestamp} | {f.source} | {f.evidence} | {f.impact} | {f.confidence} |"
        for f in selected_findings
    )

    hypotheses = build_hypotheses(system_events, keyword_hits, prayer_cache_hits, total_events)
    hypotheses_md = "\n".join(f"{idx}. {item}" for idx, item in enumerate(hypotheses, start=1))

    report = f"""# Pi4 Incident Report

## Time Window
- Start: `{event_start}`
- End: `{event_end}`
- Source directory: `{input_dir}`

## Event Summary
- SYSTEM restart cycles (shutdown->boot): **{restart_count}**
- Longest shutdown->boot downtime: **{format_seconds(max_downtime)}**
- Max event-stream gap: **{format_seconds(max_gap_sec)}**
- App ERROR lines: **{app_errors}**
- App WARNING lines: **{app_warnings}**
- SLOW_REQUEST warnings: **{slow_requests}**
- PRAYER/cache_hit events: **{prayer_cache_hits}**
- Total parsed events: **{total_events}**
- systemd NRestarts: **{service_nrestarts}**

## Suspicious Findings
| Severity | Timestamp | Source | Evidence | Possible Impact | Confidence |
| --- | --- | --- | --- | --- | --- |
{findings_rows}

## Root Cause Hypotheses (max 3)
{hypotheses_md}

## Quick Actions (next 24h)
1. Repeat the same collection immediately when a freeze appears, then compare two snapshots.
2. Confirm who sends `SIGINT` to the service (shell history, wrapper scripts, systemd stop actions).
3. Add alerting for service restarts and panel latency (`SLOW_REQUEST` count and restart spikes).
4. Reduce repetitive PRAYER logging volume (sample/aggregate) to lower I/O noise.
5. Validate power/network stability on Pi (`under-voltage`, `link flap`, `deauth`) from kernel/journal logs.

## Generated Artifacts
- `keyword_hits.txt`
- `events_system.tsv`
- `event_gap.txt`
- `incident_report.md`
"""

    write_text(report_path, report)

    events_system_path = input_dir / "events_system.tsv"
    events_system_lines = []
    for rec in system_events:
        signal = str(rec.data.get("signal", "-"))
        events_system_lines.append(f"{rec.ts_raw}\t{rec.event}\t{signal}")
    write_text(events_system_path, "\n".join(events_system_lines) + ("\n" if events_system_lines else ""))

    keyword_hits_path = input_dir / "keyword_hits.txt"
    write_text(keyword_hits_path, "\n".join(keyword_hits) + ("\n" if keyword_hits else ""))

    event_gap_path = input_dir / "event_gap.txt"
    if max_gap_from and max_gap_to:
        gap_text = (
            f"max_gap_sec={max_gap_sec}\n"
            f"from={max_gap_from.ts_raw} {max_gap_from.cat}/{max_gap_from.event}\n"
            f"to={max_gap_to.ts_raw} {max_gap_to.cat}/{max_gap_to.event}\n"
        )
    else:
        gap_text = "max_gap_sec=0\nfrom=-\nto=-\n"
    write_text(event_gap_path, gap_text)

    print(f"Report generated: {report_path}")
    print(f"Findings listed: {len(selected_findings)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
