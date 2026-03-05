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
