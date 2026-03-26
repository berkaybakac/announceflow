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
from typing import Any, Optional

logger = logging.getLogger(__name__)

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_DOTENV_PATH = os.path.join(_PROJECT_ROOT, ".env")
_DOTENV_LOADED = False

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
    "announcement_queue_gap_seconds": 10,
    "announcement_queue_max_delay_seconds": 900,
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

        load_dotenv_if_present()

        # Find config.json relative to project root
        self._config_path = os.path.join(_PROJECT_ROOT, "config.json")

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

        self._apply_env_overrides()

        self._initialized = True

    def _apply_env_overrides(self):
        """Apply optional environment variable overrides to runtime config."""
        env_media_folder = _first_non_empty_env("ANNOUNCEFLOW_MEDIA_FOLDER")
        if env_media_folder:
            self._config["media_folder"] = env_media_folder

        env_port = _coerce_int(
            os.environ.get("ANNOUNCEFLOW_WEB_PORT"), min_value=1, max_value=65535
        )
        if env_port is not None:
            self._config["web_port"] = env_port

        env_scheduler_interval = _coerce_int(
            os.environ.get("ANNOUNCEFLOW_SCHEDULER_INTERVAL_SECONDS"), min_value=1
        )
        if env_scheduler_interval is not None:
            self._config["scheduler_interval_seconds"] = env_scheduler_interval

        env_secret = _first_non_empty_env("FLASK_SECRET_KEY")
        if env_secret:
            self._config["flask_secret_key"] = env_secret

        env_admin_user = _first_non_empty_env(
            "ANNOUNCEFLOW_ADMIN_USERNAME", "ADMIN_USERNAME"
        )
        if env_admin_user:
            self._config["admin_username"] = env_admin_user

        env_admin_pass = _first_non_empty_env(
            "ANNOUNCEFLOW_ADMIN_PASSWORD", "ADMIN_PASSWORD"
        )
        if env_admin_pass:
            self._config["admin_password"] = env_admin_pass

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


def _coerce_int(
    raw_value: Any, min_value: Optional[int] = None, max_value: Optional[int] = None
) -> Optional[int]:
    """Coerce value to int and optionally validate bounds."""
    if raw_value is None:
        return None
    try:
        value = int(str(raw_value).strip())
    except (TypeError, ValueError):
        return None
    if min_value is not None and value < min_value:
        return None
    if max_value is not None and value > max_value:
        return None
    return value


def _first_non_empty_env(*keys: str) -> str:
    """Return first non-empty environment variable value for given keys."""
    for key in keys:
        value = os.environ.get(key, "")
        if isinstance(value, str):
            value = value.strip()
            if value:
                return value
    return ""


def load_dotenv_if_present(path: str = _DOTENV_PATH) -> None:
    """Load key=value pairs from .env once (without overriding real env)."""
    global _DOTENV_LOADED
    if _DOTENV_LOADED:
        return

    if not os.path.exists(path):
        _DOTENV_LOADED = True
        return

    try:
        with open(path, "r", encoding="utf-8") as f:
            for raw_line in f:
                line = raw_line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                key = key.strip()
                if not key:
                    continue
                value = value.strip()
                if (value.startswith('"') and value.endswith('"')) or (
                    value.startswith("'") and value.endswith("'")
                ):
                    value = value[1:-1]
                os.environ.setdefault(key, value)
    except OSError as e:
        logger.warning(f".env load failed: {e}")

    _DOTENV_LOADED = True
