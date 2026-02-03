"""
Base repository with shared database connection logic.
"""
import sqlite3


class BaseRepository:
    """Base class for all repositories with shared connection logic."""

    def __init__(self, db_path: str = 'announceflow.db'):
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
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn
