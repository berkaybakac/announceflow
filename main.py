"""
AnnounceFlow - Main Entry Point
Planlı Müzik & Anons Sistemi
"""
import os
import sys
import logging
import signal
import time
import socket
from logging.handlers import RotatingFileHandler

# Add current directory to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import database as db
from scheduler import get_scheduler
from player import get_player
from logger import log_system, log_error
from services.config_service import load_config
from services.release_service import load_release_stamp
from services.silence_policy import resolve_silence_policy


def _resolve_web_port(config: dict) -> int:
    """Resolve web server port from config with safe fallback."""
    raw = config.get("web_port", 5001)
    try:
        port = int(raw)
        if port < 1 or port > 65535:
            raise ValueError("out of range")
        return port
    except (TypeError, ValueError):
        logging.getLogger(__name__).warning(
            f"Invalid web_port={raw!r}; falling back to 5001"
        )
        return 5001


def _is_port_available(port: int) -> bool:
    """Return True if localhost port is available for binding."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind(("0.0.0.0", port))
            return True
        except OSError:
            return False


def _load_release_stamp(path: str = "release_stamp.json") -> dict:
    """Backward-compatible wrapper for release metadata loading."""
    return load_release_stamp(path)


def setup_logging():
    """Configure logging with file and console handlers."""
    log_file = os.environ.get("ANNOUNCEFLOW_APP_LOG_FILE", "").strip() or "announceflow.log"
    log_dir = os.path.dirname(os.path.abspath(log_file))
    if log_dir:
        os.makedirs(log_dir, exist_ok=True)

    logger = logging.getLogger()
    if logger.hasHandlers():
        logger.handlers.clear()
    logger.setLevel(logging.INFO)

    formatter = logging.Formatter(
        "%(asctime)s.%(msecs)03d - %(levelname)s - [%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # File handler
    file_handler = RotatingFileHandler(log_file, maxBytes=500_000, backupCount=3)
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    # Console handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    return logger


def main():
    """Main entry point."""
    logger = setup_logging()

    logger.info("=" * 50)
    logger.info("AnnounceFlow - Planlı Müzik & Anons Sistemi")
    logger.info("=" * 50)

    release = _load_release_stamp()
    logger.info(
        "Release stamp: ref=%s commit=%s branch=%s deployed_at=%s",
        release["ref"],
        release["commit_short"],
        release["branch"],
        release["deployed_at_utc"],
    )

    # Log system boot event
    from player import AUDIO_BACKEND

    log_system(
        "boot",
        {
            "version": "1.5.1",
            "backend": AUDIO_BACKEND,
            "release_ref": release["ref"],
            "release_commit": release["commit_short"],
            "release_branch": release["branch"],
            "deployed_at_utc": release["deployed_at_utc"],
        },
    )

    # Initialize database
    logger.info("Veritabanı başlatılıyor...")
    db.init_database()

    # Initialize player
    logger.info("Oynatıcı başlatılıyor...")
    player = get_player()

    # Restore volume from database
    # Wait for ALSA to fully initialize on Pi (prevents volume not being applied on boot)
    time.sleep(2)
    state = db.get_playback_state()
    volume = state.get("volume", 100)
    player.set_volume(volume)
    logger.info(f"Ses seviyesi ayarlandı: {volume}%")

    # Auto-restore playlist from database (resume from last state)
    playlist_state = db.get_playlist_state()
    if playlist_state.get("active") and playlist_state.get("playlist"):
        playlist = playlist_state["playlist"]
        index = playlist_state.get("index", 0)
        loop = playlist_state.get("loop", True)
        startup_config = load_config()
        startup_policy = resolve_silence_policy(
            startup_config,
            allow_network=False,
            fail_safe_on_unknown=True,
        )
        resume_allowed = not startup_policy.get("silence_active", False)

        # Filter out non-existent files
        valid_playlist = [f for f in playlist if os.path.exists(f)]

        if valid_playlist:
            logger.info(
                f"Playlist restore ediliyor: {len(valid_playlist)} şarkı, index={index}"
            )

            # Adjust index if files were removed
            if index >= len(valid_playlist):
                index = 0
            player.apply_playlist_state(
                playlist=valid_playlist,
                index=index - 1,  # Will be incremented by play_next
                loop=loop,
                runtime_active=resume_allowed,
            )

            if resume_allowed:
                # Start playing from saved position
                player.play_next()
                logger.info("Playlist otomatik başlatıldı!")
                log_system(
                    "playlist_restore", {"tracks": len(valid_playlist), "index": index}
                )
            else:
                # Defer until silence policy allows playback
                scheduler = get_scheduler()
                pause_state = {
                    "playlist": list(valid_playlist),
                    "index": index,
                    "loop": loop,
                    "active": True,
                }
                if startup_policy.get("policy") == "working_hours":
                    scheduler.defer_playlist_restore("working_hours", pause_state)
                    logger.info("Mesai dışında: playlist otomatik başlatılmadı.")
                else:
                    scheduler.defer_playlist_restore("prayer", pause_state)
                    logger.info("Sessizlik policy aktif: playlist otomatik başlatılmadı.")
                log_system(
                    "playlist_restore_deferred",
                    {
                        "tracks": len(valid_playlist),
                        "index": index,
                        "policy": startup_policy.get("policy"),
                        "reason_code": startup_policy.get("reason_code"),
                        "source": startup_policy.get("source"),
                        "fail_safe_applied": startup_policy.get("fail_safe_applied"),
                    },
                )
                if startup_policy.get("fail_safe_applied"):
                    log_error(
                        "policy_fail_safe_engaged",
                        {
                            "context": "startup_restore",
                            "policy": startup_policy.get("policy"),
                            "reason_code": startup_policy.get("reason_code"),
                            "source": startup_policy.get("source"),
                        },
                    )
        else:
            logger.warning("Kaydedilmiş playlist'teki dosyalar bulunamadı")
            log_error("playlist_restore_failed", {"reason": "files_not_found"})

    # Initialize scheduler
    logger.info("Zamanlayıcı başlatılıyor...")
    scheduler = get_scheduler()
    scheduler.start()

    # Signal handlers
    def graceful_exit(signum, frame):
        logger.info("Kapatma sinyali alındı. Sistem durduruluyor...")
        signal_name = "SIGINT" if signum == signal.SIGINT else "SIGTERM"
        log_system("shutdown", {"signal": signal_name})
        
        # Stop stream receiver and playback
        try:
            from services.stream_service import get_stream_service
            get_stream_service().stop()
        except Exception as exc:
            logger.debug("WebStream: shutdown error: %s", exc)

        scheduler.stop()
        player.stop()
        sys.exit(0)

    signal.signal(signal.SIGINT, graceful_exit)
    signal.signal(signal.SIGTERM, graceful_exit)

    # Start web panel
    runtime_config = load_config()
    web_port = _resolve_web_port(runtime_config)
    if not _is_port_available(web_port):
        if web_port != 5001 and _is_port_available(5001):
            logger.warning(
                f"Port {web_port} kullanımda; güvenli fallback olarak 5001 seçildi."
            )
            web_port = 5001
        else:
            logger.error(
                f"Port {web_port} kullanımda ve fallback portu da uygun değil. Başlatma iptal edildi."
            )
            sys.exit(1)

    logger.info(f"Web panel başlatılıyor (Port {web_port})...")
    logger.info(f"Tarayıcıda açın: http://localhost:{web_port}")
    logger.info("-" * 50)

    # Import and run web panel
    from web_panel import app

    dev_reload = os.environ.get("ANNOUNCEFLOW_DEV_RELOAD") == "1"

    if dev_reload:
        logger.warning("Dev auto-reload açık (ANNOUNCEFLOW_DEV_RELOAD=1).")
        app.run(host="0.0.0.0", port=web_port, debug=True, use_reloader=True)
    else:
        try:
            from waitress import serve

            # Optimized configuration for Pi 4 production
            serve(
                app,
                host="0.0.0.0",
                port=web_port,
                threads=16,
                connection_limit=200,
                channel_timeout=10,
                _quiet=True,
            )
        except ImportError:
            logger.warning("Waitress bulunamadı, Flask development server kullanılıyor...")
            app.run(host="0.0.0.0", port=web_port, debug=False)


if __name__ == "__main__":
    main()
