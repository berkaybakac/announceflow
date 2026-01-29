"""
AnnounceFlow - Main Entry Point
Planlı Müzik & Anons Sistemi
"""
import os
import sys
import logging
import signal
from logging.handlers import RotatingFileHandler

# Add current directory to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import database as db
from scheduler import get_scheduler
from player import get_player


def setup_logging():
    """Configure logging with file and console handlers."""
    log_file = 'announceflow.log'
    
    logger = logging.getLogger()
    if logger.hasHandlers():
        logger.handlers.clear()
    logger.setLevel(logging.INFO)
    
    formatter = logging.Formatter(
        '%(asctime)s - %(levelname)s - [%(name)s] %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
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
    
    # Initialize database
    logger.info("Veritabanı başlatılıyor...")
    db.init_database()
    
    # Initialize player
    logger.info("Oynatıcı başlatılıyor...")
    player = get_player()
    
    # Restore volume from database
    state = db.get_playback_state()
    if state.get('volume'):
        player.set_volume(state['volume'])
    
    # Initialize scheduler
    logger.info("Zamanlayıcı başlatılıyor...")
    scheduler = get_scheduler()
    scheduler.start()
    
    # Signal handlers
    def graceful_exit(signum, frame):
        logger.info("Kapatma sinyali alındı. Sistem durduruluyor...")
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
    
    try:
        from waitress import serve
        # Optimized configuration for Pi 4 production
        serve(app, host='0.0.0.0', port=5001, threads=16, connection_limit=200, channel_timeout=10, _quiet=True)
    except ImportError:
        logger.warning("Waitress bulunamadı, Flask development server kullanılıyor...")
        app.run(host='0.0.0.0', port=5001, debug=False)


if __name__ == "__main__":
    main()
