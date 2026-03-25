"""
Runtime volume overlay service.

Keeps an ephemeral "announcement mute override" state that does NOT mutate
canonical DB intent. Canonical state stays in playback_state.volume fields.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Any, Optional

import database as db
from player import get_player


logger = logging.getLogger(__name__)


def _clamp_volume(value: Any, default: int = 80) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = int(default)
    return max(0, min(100, parsed))


class VolumeRuntimeService:
    """Manage temporary mute override during announcements."""

    def __init__(self):
        self._lock = threading.Lock()
        self._override_active = False
        self._override_token = 0
        self._override_session: Optional[int] = None
        self._override_volume = 0
        self._override_source = ""

    def activate_announcement_override(
        self,
        *,
        playback_session: Optional[int],
        effective_volume: int,
        source: str,
    ) -> bool:
        """Mark temporary override active for the current playback session."""
        volume = max(1, _clamp_volume(effective_volume))
        with self._lock:
            self._override_token += 1
            token = self._override_token
            self._override_active = True
            self._override_session = playback_session
            self._override_volume = volume
            self._override_source = str(source or "announcement")

        self._start_session_watcher(token)
        logger.info(
            "Volume override activated: source=%s session=%s volume=%s",
            source,
            playback_session,
            volume,
        )
        return True

    def cancel_override(self, *, reason: str, restore: bool = False) -> bool:
        """Cancel override state (optionally restoring canonical output volume)."""
        if restore:
            return self.restore_override(reason=reason)

        with self._lock:
            if not self._override_active:
                return False
            self._override_active = False
            self._override_session = None
            self._override_volume = 0
            self._override_source = ""
            self._override_token += 1

        logger.info("Volume override cancelled: reason=%s", reason)
        return True

    def restore_override(self, *, reason: str, token: Optional[int] = None) -> bool:
        """Deactivate override and restore player output to canonical DB volume."""
        with self._lock:
            if not self._override_active:
                return False
            if token is not None and token != self._override_token:
                return False
            self._override_active = False
            self._override_session = None
            self._override_volume = 0
            self._override_source = ""
            self._override_token += 1

        canonical = db.get_volume_state()
        target_volume = _clamp_volume(canonical.get("volume", 80))
        try:
            get_player().set_volume(target_volume)
        except (OSError, RuntimeError) as exc:  # pragma: no cover - defensive
            logger.warning("Volume override restore failed: %s", exc)
            return False

        logger.info(
            "Volume override restored canonical volume: reason=%s volume=%s",
            reason,
            target_volume,
        )
        return True

    def get_effective_state(
        self, canonical_state: Optional[dict], player_volume: Optional[int]
    ) -> dict:
        """Compute effective output fields for clients."""
        canonical_state = canonical_state or {}
        canonical_volume = _clamp_volume(canonical_state.get("volume", 80))
        canonical_muted = bool(canonical_state.get("muted", canonical_volume <= 0))

        with self._lock:
            override_active = bool(self._override_active)
            override_volume = _clamp_volume(self._override_volume, default=canonical_volume)

        if override_active:
            effective_volume = _clamp_volume(
                player_volume if player_volume is not None else override_volume,
                default=override_volume,
            )
            if effective_volume <= 0:
                effective_volume = max(1, override_volume)
            return {
                "effective_volume": effective_volume,
                "effective_muted": False,
                "mute_override_active": True,
            }

        return {
            "effective_volume": canonical_volume,
            "effective_muted": canonical_muted,
            "mute_override_active": False,
        }

    def _start_session_watcher(self, token: int) -> None:
        thread = threading.Thread(
            target=self._watch_session_for_override_end,
            args=(token,),
            daemon=True,
        )
        thread.start()

    def _watch_session_for_override_end(self, token: int) -> None:
        deadline = time.monotonic() + 3600.0
        while time.monotonic() < deadline:
            with self._lock:
                if not self._override_active or token != self._override_token:
                    return
                target_session = self._override_session

            player = get_player()
            current_session = getattr(player, "_playback_session", None)
            if target_session is None:
                if not player.is_playing:
                    self.restore_override(reason="announcement_stopped", token=token)
                    return
            elif current_session != target_session:
                self.restore_override(reason="announcement_session_changed", token=token)
                return
            time.sleep(0.2)

        self.restore_override(reason="announcement_override_timeout", token=token)


_volume_runtime_service: Optional[VolumeRuntimeService] = None


def get_volume_runtime_service() -> VolumeRuntimeService:
    """Get singleton runtime volume service."""
    global _volume_runtime_service
    if _volume_runtime_service is None:
        _volume_runtime_service = VolumeRuntimeService()
    return _volume_runtime_service

