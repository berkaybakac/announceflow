"""
AnnounceFlow - Main Entry Point
Planlı Müzik & Anons Sistemi
"""
import os
import sys
import logging
import signal
import time
from logging.handlers import RotatingFileHandler

# Add current directory to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import database as db
from scheduler import get_scheduler
from player import get_player
from logger import log_system, log_error


def setup_logging():
    """Configure logging with file and console handlers."""
    log_file = "announceflow.log"

    logger = logging.getLogger()
    if logger.hasHandlers():
        logger.handlers.clear()
    logger.setLevel(logging.INFO)

    formatter = logging.Formatter(
        "%(asctime)s - %(levelname)s - [%(name)s] %(message)s",
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

    # Log system boot event
    from player import AUDIO_BACKEND

    log_system("boot", {"version": "1.5.1", "backend": AUDIO_BACKEND})

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

        # Filter out non-existent files
        valid_playlist = [f for f in playlist if os.path.exists(f)]

        if valid_playlist:
            logger.info(
                f"Playlist restore ediliyor: {len(valid_playlist)} şarkı, index={index}"
            )
            player._playlist = valid_playlist
            player._playlist_loop = loop
            player._playlist_active = True

            # Adjust index if files were removed
            if index >= len(valid_playlist):
                index = 0
            player._playlist_index = index - 1  # Will be incremented by play_next

            # Start playing from saved position
            player.play_next()
            logger.info("Playlist otomatik başlatıldı!")
            log_system(
                "playlist_restore", {"tracks": len(valid_playlist), "index": index}
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
        scheduler.stop()
        player.stop()
        sys.exit(0)

    signal.signal(signal.SIGINT, graceful_exit)
    signal.signal(signal.SIGTERM, graceful_exit)

    # Start web panel
    logger.info("Web panel başlatılıyor (Port 5001)...")
    logger.info("Tarayıcıda açın: http://localhost:5001")
    logger.info("-" * 50)

    # Import and run web panel
    from web_panel import app

    dev_reload = os.environ.get("ANNOUNCEFLOW_DEV_RELOAD") == "1"

    if dev_reload:
        logger.warning("Dev auto-reload açık (ANNOUNCEFLOW_DEV_RELOAD=1).")
        app.run(host="0.0.0.0", port=5001, debug=True, use_reloader=True)
    else:
        try:
            from waitress import serve

            # Optimized configuration for Pi 4 production
            serve(
                app,
                host="0.0.0.0",
                port=5001,
                threads=16,
                connection_limit=200,
                channel_timeout=10,
                _quiet=True,
            )
        except ImportError:
            logger.warning("Waitress bulunamadı, Flask development server kullanılıyor...")
            app.run(host="0.0.0.0", port=5001, debug=False)


if __name__ == "__main__":
    main()
