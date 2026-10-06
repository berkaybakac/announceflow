"""Pytest session defaults for runtime/log isolation."""
from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path


_RUNTIME_ROOT = Path(tempfile.gettempdir()) / f"announceflow_pytest_runtime_{os.getpid()}"
_LOG_DIR = _RUNTIME_ROOT / "logs"
_AGENT_RUNTIME = _RUNTIME_ROOT / "agent_runtime"

# Clean previous leftovers from same PID and create fresh dirs.
shutil.rmtree(_RUNTIME_ROOT, ignore_errors=True)
_LOG_DIR.mkdir(parents=True, exist_ok=True)
_AGENT_RUNTIME.mkdir(parents=True, exist_ok=True)

# Keep tests away from production/dev runtime logs.
os.environ["ANNOUNCEFLOW_LOG_DIR"] = str(_LOG_DIR)
os.environ["ANNOUNCEFLOW_EVENT_LOG_FILE"] = str(_LOG_DIR / "events.jsonl")
os.environ["ANNOUNCEFLOW_APP_LOG_FILE"] = str(_RUNTIME_ROOT / "announceflow.log")
os.environ["ANNOUNCEFLOW_AGENT_RUNTIME_DIR"] = str(_AGENT_RUNTIME)

# Point the database package at a session-scoped temp DB before anything
# touches it. `database.DATABASE_PATH` defaults to the relative
# "announceflow.db", i.e. the real dev DB in the repo root, and the repository
# singletons capture that path at import time — so both must be swapped.
# Tests that swap in their own DB save/restore whatever is current, so they
# now restore back to this temp DB instead of the dev one.
import pytest  # noqa: E402

import database  # noqa: E402
from database.media_repository import MediaRepository  # noqa: E402
from database.playback_repository import PlaybackRepository  # noqa: E402
from database.schedule_repository import ScheduleRepository  # noqa: E402
from database.usage_repository import UsageRepository  # noqa: E402


def point_database_at(path: str) -> None:
    """Repoint `database` module globals and repository singletons at `path`."""
    database.DATABASE_PATH = path
    database._media_repo = MediaRepository(path)
    database._schedule_repo = ScheduleRepository(path)
    database._playback_repo = PlaybackRepository(path)
    database._usage_repo = UsageRepository(path)


_SESSION_DB_PATH = str(_RUNTIME_ROOT / "announceflow_test.db")
point_database_at(_SESSION_DB_PATH)

# Several tests assume the schema already exists (matching how main.py always
# calls this on real boot) instead of isolating their own DB.
database.init_database()


@pytest.fixture
def repoint_db():
    """`point_database_at` for tests; combine with `temp_db` so it gets restored."""
    return point_database_at


@pytest.fixture
def temp_db(tmp_path):
    """Fresh, initialised DB for one test; restores the session DB afterwards."""
    saved = (
        database.DATABASE_PATH,
        database._media_repo,
        database._schedule_repo,
        database._playback_repo,
        database._usage_repo,
    )
    path = str(tmp_path / "announceflow.db")
    point_database_at(path)
    database.init_database()
    try:
        yield path
    finally:
        (
            database.DATABASE_PATH,
            database._media_repo,
            database._schedule_repo,
            database._playback_repo,
            database._usage_repo,
        ) = saved
