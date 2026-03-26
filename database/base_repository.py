"""
Base repository with shared database connection logic.
"""
import os
import sqlite3


class BaseRepository:
    """Base class for all repositories with shared connection logic."""

    def __init__(self, db_path: str = "announceflow.db"):
        """Initialize repository with database path.

        Args:
            db_path: Path to SQLite database file
        """
        self.db_path = db_path

    def get_connection(self) -> sqlite3.Connection:
        """Get database connection with row factory.

        Returns:
            SQLite connection with Row factory enabled
        """
        timeout_seconds = float(
            os.environ.get("ANNOUNCEFLOW_SQLITE_TIMEOUT_SECONDS", "15")
        )
        busy_timeout_ms = int(
            os.environ.get("ANNOUNCEFLOW_SQLITE_BUSY_TIMEOUT_MS", "15000")
        )

        conn = sqlite3.connect(self.db_path, timeout=timeout_seconds)
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute(f"PRAGMA busy_timeout = {busy_timeout_ms}")
        conn.execute("PRAGMA journal_mode = WAL")
        conn.row_factory = sqlite3.Row
        return conn
