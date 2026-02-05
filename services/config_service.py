"""
Centralized configuration management.
Features:
- Singleton pattern
- Fallback to defaults
- Atomic write (temp file + rename)
- Backward compatible with existing config.json
"""
import json
import os
import tempfile
import shutil
import logging
from typing import Any

logger = logging.getLogger(__name__)

# Default values (fallback if key missing)
DEFAULTS = {
    "volume": 100,
    "admin_username": "admin",
    "admin_password": "admin123",
    "database_path": "announceflow.db",
    "media_folder": "media",
    "web_port": 5001,
    "scheduler_interval_seconds": 10,
    "working_hours_enabled": False,
    "working_hours_start": "09:00",
    "working_hours_end": "22:00",
    "prayer_times_enabled": False,
    "prayer_times_city": "Istanbul",
    "prayer_times_district": "Istanbul",
    # Future: device_id, mode, central_server
}


class ConfigService:
    _instance = None
    _config_path = None
    _config = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def _ensure_loaded(self):
        """Lazy load config on first access."""
        if self._initialized:
            return

        # Find config.json relative to project root
        self._config_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config.json"
        )

        self._config = dict(DEFAULTS)  # Start with defaults

        if os.path.exists(self._config_path):
            try:
                with open(self._config_path, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                    self._config.update(loaded)  # Override defaults
                logger.info(f"Config loaded from {self._config_path}")
            except (json.JSONDecodeError, IOError) as e:
                logger.error(f"Config load failed, using defaults: {e}")
        else:
            logger.warning(f"Config not found at {self._config_path}, using defaults")

        self._initialized = True

    def get(self, key: str, default: Any = None) -> Any:
        """Get config value with fallback."""
        self._ensure_loaded()
        # Priority: config file > DEFAULTS > provided default
        if key in self._config:
            return self._config[key]
        return default

    def set(self, key: str, value: Any) -> bool:
        """Set config value with atomic write."""
        self._ensure_loaded()
        self._config[key] = value
        return self._save()

    def get_all(self) -> dict:
        """Get all config values."""
        self._ensure_loaded()
        return dict(self._config)

    def update_all(self, config_dict: dict) -> bool:
        """Update multiple config values at once with atomic write."""
        self._ensure_loaded()
        self._config.update(config_dict)
        return self._save()

    def _save(self) -> bool:
        """Atomic write: write to temp file, then rename."""
        if not self._config_path:
            return False

        temp_path = None
        try:
            # Write to temp file in same directory (for atomic rename)
            dir_name = os.path.dirname(self._config_path)
            fd, temp_path = tempfile.mkstemp(dir=dir_name, suffix=".tmp")

            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(self._config, f, indent=4, ensure_ascii=False)

            # Atomic rename (POSIX) or replace (Windows)
            shutil.move(temp_path, self._config_path)
            logger.info(f"Config saved to {self._config_path}")
            return True

        except (IOError, OSError) as e:
            logger.error(f"Config save failed: {e}")
            # Clean up temp file if exists
            if temp_path and os.path.exists(temp_path):
                try:
                    os.remove(temp_path)
                except OSError:
                    pass
            return False

    def reload(self):
        """Force reload from disk."""
        self._initialized = False
        self._ensure_loaded()


def get_config() -> ConfigService:
    """Get singleton config instance."""
    return ConfigService()


# For backward compatibility: allow direct function calls
def load_config() -> dict:
    """Legacy function - returns config as dict."""
    return get_config().get_all()


def save_config(config_dict: dict) -> bool:
    """Legacy function - saves entire config dict with atomic write."""
    return get_config().update_all(config_dict)
