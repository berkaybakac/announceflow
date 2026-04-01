#!/usr/bin/env python3
"""
AnnounceFlow - Diagnostic & Health Tool (diagnose.py)
---------------------------------------------------
Parses logs/events.jsonl and provides a terminal-friendly health scoreboard.
"""

import os
import sys
import json
import time
from datetime import datetime, timedelta, timezone

# Standard configuration
LOG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs", "events.jsonl")
DEFAULT_LOOKBACK_MINUTES = 60

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

def get_summary_data(minutes=60):
    """Core analysis logic, returns a dict of stats."""
    if not os.path.exists(LOG_FILE):
        return None

    cutoff = datetime.now(timezone.utc) - timedelta(minutes=minutes)
    stats = {
        "xruns": 0,
        "jitters": 0,
        "ping_warnings": 0,
        "temps": [],
        "cpu_loads": [],
        "wifi_signals": [],
        "tracks_played": 0,
        "tracks_skipped": 0,
        "last_health": None,
        "total_entries": 0,
        "lookback_minutes": minutes
    }

    try:
        with open(LOG_FILE, "r") as f:
            for line in f:
                try:
                    entry = json.loads(line)
                    ts_raw = entry.get("ts")
                    if not ts_raw:
                        continue
                    
                    ts = _parse_iso(ts_raw)
                    if not ts or ts < cutoff:
                        continue
                    
                    stats["total_entries"] += 1
                    event = entry.get("event")
                    data = entry.get("data", {})

                    if event == "xrun_snapshot":
                        stats["xruns"] += 1
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

                except (json.JSONDecodeError, KeyError):
                    continue
    except Exception:
        return None
    
    return stats

def analyze_history(minutes=60):
    stats = get_summary_data(minutes)
    if stats is None:
        print(f"ERROR: Log file not found or unreadable at {LOG_FILE}")
        return
    _print_report(stats, minutes)

def _print_report(s, minutes):
    print("\n" + "="*50)
    print(f" ANNOUNCEFLOW LOG ANALİZİ (Son {minutes} dakika)")
    print("="*50)

    # 1. Donanım Özeti
    print("\n[DONANIM SAĞLIĞI]")
    if s["temps"]:
        avg_temp = round(sum(s["temps"]) / len(s["temps"]), 1)
        max_temp = max(s["temps"])
        temp_status = "TAMAM" if max_temp < 75 else "UYARI (Yüksek Isı!)"
        print(f"  - İşlemci Isısı: {avg_temp}°C (Peak: {max_temp}°C) -> {temp_status}")
    else:
        print("  - İşlemci Isısı: Veri yok")

    if s["cpu_loads"]:
        avg_load = round(sum(s["cpu_loads"]) / len(s["cpu_loads"]), 2)
        print(f"  - İşlemci Yükü (1m avg): {avg_load}")

    if s["wifi_signals"]:
        avg_signal = round(sum(s["wifi_signals"]) / len(s["wifi_signals"]), 1)
        print(f"  - WiFi Sinyali: {avg_signal} dBm")
    
    # 2. Ses Kalitesi Analizi
    print("\n[SES KALİTESİ & NETWORK]")
    print(f"  - Ses Kesilmesi (XRUN): {s['xruns']} adet")
    print(f"  - Ağ Dalgalanması (JITTER): {s['jitters']} adet")
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
    if s["xruns"] > 5: reasons.append("Ses donanımı (alsa) çok sık kesiliyor.")
    if s["jitters"] > 5: reasons.append("Ağ bağlantınız stabil değil (jitter yüksek).")
    if s["ping_warnings"] > 3: reasons.append("PC (Gönderici) uyku moduna geçiyor veya gecikme yapıyor.")
    if any(t > 80 for t in s["temps"]): reasons.append("Cihaz aşırı ısınıyor (Sıcaklık 80+).")
    
    if not reasons:
        print(" [+] SİSTEM MÜKEMMEL: Herhangi bir problem tespit edilmedi.")
    else:
        for r in reasons:
            print(f" [!] {r}")
    
    print("="*50 + "\n")

if __name__ == "__main__":
    minutes = DEFAULT_LOOKBACK_MINUTES
    if len(sys.argv) > 1:
        try:
            minutes = int(sys.argv[1])
        except ValueError:
            pass
    analyze_history(minutes)
