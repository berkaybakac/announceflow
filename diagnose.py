#!/usr/bin/env python3
"""
AnnounceFlow - Diagnostic & Health Tool (diagnose.py)
---------------------------------------------------
Parses logs/events.jsonl and provides a terminal-friendly health scoreboard.
"""

import argparse
import glob
import os
import json
import time
from datetime import datetime, timedelta, timezone

# Standard configuration
LOG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs", "events.jsonl")
DEFAULT_LOOKBACK_MINUTES = 60


def resolve_log_file(file=None, dump_dir=None):
    """Pick the events.jsonl to analyze: explicit --file, a pulled dump
    --dir (logs/events.jsonl inside it), or the local device default."""
    if file:
        return file
    if dump_dir:
        candidate = os.path.join(dump_dir, "logs", "events.jsonl")
        if os.path.exists(candidate):
            return candidate
        return os.path.join(dump_dir, "events.jsonl")
    return LOG_FILE

def _parse_iso(iso_str):
    """Parse ISO timestamp to UTC datetime object reliably."""
    try:
        # Standardize Zulu suffix to ISO-8601 offset
        if iso_str.endswith("Z"):
            iso_str = iso_str.replace("Z", "+00:00")
        dt = datetime.fromisoformat(iso_str)
        # Ensure offset-aware for comparison
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None

def get_summary_data(minutes=60, file=None):
    """Core analysis logic, returns a dict of stats. `file` overrides the
    local device log path — pass a pulled dump's events.jsonl to analyze it."""
    log_file_path = file or LOG_FILE
    if not os.path.exists(log_file_path):
        return None

    cutoff = datetime.now(timezone.utc) - timedelta(minutes=minutes)
    stats = {
        "xruns": 0,
        "xrun_hours": 0.0,  # summed session duration backing "xruns", for a rate
        "jitters": 0,
        "ping_warnings": 0,
        "temps": [],
        "cpu_loads": [],
        "wifi_signals": [],
        "tracks_played": 0,
        "tracks_skipped": 0,
        "last_health": None,
        "total_entries": 0,
        "lookback_minutes": minutes,
        # Metric families with zero backing events anywhere in the read log
        # (not just this window) — a build/version gap, not "0 = healthy".
        "unsupported": [],
    }

    # Rotated backups (events.jsonl.1, .2, ...) can hold entries within the
    # lookback window too; without them a "last 24h" query can silently miss
    # data once the current file has rotated out that history.
    log_files = sorted(glob.glob(log_file_path + "*"))

    # Every event name seen anywhere in the read files, regardless of the
    # time window — used only to tell "this build doesn't emit this metric"
    # apart from "this window happened to have zero incidents".
    seen_events = set()

    try:
        for log_file in log_files:
            with open(log_file, "r") as f:
                for line in f:
                    try:
                        entry = json.loads(line)
                    except json.JSONDecodeError:
                        continue

                    event = entry.get("event")
                    if event:
                        seen_events.add(event)

                    ts_raw = entry.get("ts")
                    if not ts_raw:
                        continue
                    ts = _parse_iso(ts_raw)
                    if not ts or ts < cutoff:
                        continue

                    stats["total_entries"] += 1
                    data = entry.get("data", {}) or {}

                    if event == "stream_receiver_summary":
                        # Unthrottled, per-session xrun count. Present since
                        # v2.2.0 (2026-03), unlike the throttled xrun_snapshot
                        # alarm event, which only exists from 2026-04-01 on.
                        stats["xruns"] += int(data.get("alsa_xrun") or 0)
                        stats["xrun_hours"] += float(data.get("duration_seconds") or 0) / 3600.0
                    elif event == "stream_jitter_anomaly":
                        stats["jitters"] += 1
                    elif event == "sender_ping_latency_high":
                        stats["ping_warnings"] += 1
                    elif event == "system_health":
                        stats["last_health"] = data
                        if data.get("temp_c", -1) > 0:
                            stats["temps"].append(data["temp_c"])
                        if data.get("load_1m", -1) >= 0:
                            stats["cpu_loads"].append(data["load_1m"])
                        wifi_signal = data.get("wifi_signal_dbm", -1)
                        if isinstance(wifi_signal, (int, float)) and wifi_signal != -1 and -100 <= wifi_signal <= 0:
                            stats["wifi_signals"].append(wifi_signal)
                    elif event == "track_end":
                        stats["tracks_played"] += 1
                    elif event == "tracks_skipped":
                        stats["tracks_skipped"] += 1
                    elif event in ("playlist_track_missing", "playlist_track_start_failed"):
                        stats["tracks_skipped"] += 1
                    elif event == "playback_usage_audit":
                        status = str(data.get("status", "")).strip().lower()
                        if status in {"interrupted", "stopped"}:
                            stats["tracks_skipped"] += 1

    except Exception:
        return None

    if not ({"stream_receiver_summary", "stream_receiver_alsa_xrun"} & seen_events):
        stats["unsupported"].append("xruns")
    if "stream_jitter_anomaly" not in seen_events:
        stats["unsupported"].append("jitters")
    if "sender_ping_latency_high" not in seen_events:
        stats["unsupported"].append("ping_warnings")
    if "system_health" not in seen_events:
        stats["unsupported"].append("system_health")

    return stats

def analyze_history(minutes=60, file=None):
    log_file_path = file or LOG_FILE
    stats = get_summary_data(minutes, file=file)
    if stats is None:
        print(f"ERROR: Log file not found or unreadable at {log_file_path}")
        return
    print(f"(kaynak: {log_file_path})")
    _print_report(stats, minutes)

_UNSUPPORTED_LABELS = {
    "xruns": "XRUN (ses kesilmesi)",
    "jitters": "ağ dalgalanması (jitter)",
    "ping_warnings": "gönderici PC gecikme uyarısı",
    "system_health": "donanım sağlığı (ısı/CPU/WiFi)",
}


def _print_report(s, minutes):
    unsupported = set(s.get("unsupported", []))
    print("\n" + "="*50)
    print(f" ANNOUNCEFLOW LOG ANALİZİ (Son {minutes} dakika)")
    print("="*50)

    # 1. Donanım Özeti
    print("\n[DONANIM SAĞLIĞI]")
    if "system_health" in unsupported:
        print("  - Veri yok: bu log setinde donanım sağlığı hiç kaydedilmemiş")
        print("    (eski build, 2026-04-01 öncesi — telemetri o build'de yok)")
    elif s["temps"]:
        avg_temp = round(sum(s["temps"]) / len(s["temps"]), 1)
        max_temp = max(s["temps"])
        temp_status = "TAMAM" if max_temp < 75 else "UYARI (Yüksek Isı!)"
        print(f"  - İşlemci Isısı: {avg_temp}°C (Peak: {max_temp}°C) -> {temp_status}")
        if s["cpu_loads"]:
            avg_load = round(sum(s["cpu_loads"]) / len(s["cpu_loads"]), 2)
            print(f"  - İşlemci Yükü (1m avg): {avg_load}")
        if s["wifi_signals"]:
            avg_signal = round(sum(s["wifi_signals"]) / len(s["wifi_signals"]), 1)
            print(f"  - WiFi Sinyali: {avg_signal} dBm")
    else:
        print("  - Bu pencerede kayıt yok (build destekliyor, henüz veri gelmemiş)")

    # 2. Ses Kalitesi Analizi
    print("\n[SES KALİTESİ & NETWORK]")
    xrun_rate = (s["xruns"] / s["xrun_hours"]) if s.get("xrun_hours") else None
    if "xruns" in unsupported:
        print("  - Ses Kesilmesi (XRUN): Veri yok (bu build'de xrun telemetrisi yok)")
    elif xrun_rate is not None:
        print(f"  - Ses Kesilmesi (XRUN): {s['xruns']} adet (saatte {xrun_rate:.1f})")
    else:
        print(f"  - Ses Kesilmesi (XRUN): {s['xruns']} adet")

    if "jitters" in unsupported:
        print("  - Ağ Dalgalanması (JITTER): Veri yok (bu build'de bu telemetri yok)")
    else:
        print(f"  - Ağ Dalgalanması (JITTER): {s['jitters']} adet")

    if "ping_warnings" in unsupported:
        print("  - PC Gecikme Uyarıları: Veri yok (bu build'de bu telemetri yok)")
    else:
        print(f"  - PC Gecikme Uyarıları: {s['ping_warnings']} adet")

    # 3. Oynatma Karnesi
    print("\n[OYNATMA İSTATİSTİKLERİ]")
    total_tracks = s["tracks_played"] + s["tracks_skipped"]
    if total_tracks > 0:
        success_rate = round((s["tracks_played"] / total_tracks) * 100, 1)
        print(f"  - Tamamlanan Şarkı: {s['tracks_played']}")
        print(f"  - Atlanan Şarkı: {s['tracks_skipped']}")
        print(f"  - Başarı Oranı: %{success_rate}")
    else:
        print("  - Henüz oynatma verisi yok.")

    # 4. Sonuç & Tavsiye
    print("\n" + "-"*50)
    print(" KESİN TEŞHİS:")

    reasons = []
    if "xruns" not in unsupported:
        if xrun_rate is not None and xrun_rate > 5:
            reasons.append(f"Ses donanımı (alsa) sık kesiliyor (saatte {xrun_rate:.1f} xrun).")
        elif xrun_rate is None and s["xruns"] > 5:
            reasons.append("Ses donanımı (alsa) çok sık kesiliyor.")
    if "jitters" not in unsupported and s["jitters"] > 5:
        reasons.append("Ağ bağlantınız stabil değil (jitter yüksek).")
    if "ping_warnings" not in unsupported and s["ping_warnings"] > 3:
        reasons.append("PC (Gönderici) uyku moduna geçiyor veya gecikme yapıyor.")
    if "system_health" not in unsupported and any(t > 80 for t in s["temps"]):
        reasons.append("Cihaz aşırı ısınıyor (Sıcaklık 80+).")

    for r in reasons:
        print(f" [!] {r}")

    if unsupported:
        missing = ", ".join(_UNSUPPORTED_LABELS.get(k, k) for k in sorted(unsupported))
        print(f" [?] KISITLI TEŞHİS: {missing} için bu veri setinde hiç ölçüm yok.")
        print("     Bu metrikler değerlendirme dışı — 'sorun yok' anlamına gelmez.")
        if not reasons:
            print(" [+] Ölçülebilen metriklerde sorun tespit edilmedi.")
    elif not reasons:
        print(" [+] SİSTEM MÜKEMMEL: Herhangi bir problem tespit edilmedi.")

    print("="*50 + "\n")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="AnnounceFlow diagnostic scoreboard")
    parser.add_argument(
        "minutes", nargs="?", type=int, default=DEFAULT_LOOKBACK_MINUTES,
        help="Lookback window in minutes (default: 60)",
    )
    parser.add_argument("--file", help="Path to a specific events.jsonl")
    parser.add_argument(
        "--dir", help="Pulled field dump directory (uses <dir>/logs/events.jsonl)"
    )
    args = parser.parse_args()

    analyze_history(args.minutes, file=resolve_log_file(args.file, args.dir))
