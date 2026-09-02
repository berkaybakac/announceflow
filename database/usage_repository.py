"""
Usage session history repository (music/stream/announcement durations).
"""
import json
import logging
from typing import Any, Dict, Optional

from .base_repository import BaseRepository

logger = logging.getLogger(__name__)


class UsageRepository(BaseRepository):
    """Repository for persisted usage session history."""

    def record_session(
        self,
        kind: str,
        started_at: str,
        ended_at: str,
        duration_seconds: float,
        source: Optional[str] = None,
        detail: Optional[Dict[str, Any]] = None,
        ended_reason: Optional[str] = None,
        gap_seconds: Optional[float] = None,
    ) -> None:
        """Insert one usage session row. Never raises — logs and swallows on failure,
        so a DB hiccup never breaks the playback/stream stop path calling this."""
        try:
            conn = self.get_connection()
            try:
                conn.execute(
                    """
                    INSERT INTO usage_sessions
                        (kind, started_at, ended_at, duration_seconds, source,
                         detail_json, ended_reason, gap_seconds)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        kind,
                        started_at,
                        ended_at,
                        duration_seconds,
                        source,
                        json.dumps(detail, ensure_ascii=False) if detail else None,
                        ended_reason,
                        gap_seconds,
                    ),
                )
                conn.commit()
            finally:
                conn.close()
        except Exception as exc:
            logger.debug("usage_sessions insert failed: %s", exc)
