"""events.jsonl has exactly one rotator: the main process.

The stream receiver is a separate process that imports logger.py. When it
also opened a RotatingFileHandler on the same file, the two processes
rotated it independently (writes landing in the renamed `.1`, backups
shifted twice). With ANNOUNCEFLOW_EVENT_LOG_NO_ROTATE=1 the receiver
appends to whatever file is currently events.jsonl and never rotates.
Run in subprocesses so the module-level handler setup is exercised fresh.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def _run_child(tmp_path: Path, script: str, no_rotate: bool, max_bytes: int = 5_000_000) -> str:
    env = os.environ.copy()
    env["ANNOUNCEFLOW_EVENT_LOG_FILE"] = str(tmp_path / "events.jsonl")
    env["ANNOUNCEFLOW_LOG_DIR"] = str(tmp_path)
    env["ANNOUNCEFLOW_EVENT_LOG_MAX_BYTES"] = str(max_bytes)
    env["PYTHONPATH"] = str(REPO_ROOT)
    if no_rotate:
        env["ANNOUNCEFLOW_EVENT_LOG_NO_ROTATE"] = "1"
    else:
        env.pop("ANNOUNCEFLOW_EVENT_LOG_NO_ROTATE", None)
    result = subprocess.run(
        [sys.executable, "-c", script], env=env, cwd=tmp_path,
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def _events(path: Path) -> list:
    if not path.exists():
        return []
    return [json.loads(line)["event"] for line in path.read_text().splitlines() if line.strip()]


def test_main_process_keeps_rotating_handler(tmp_path):
    out = _run_child(tmp_path, "import logger; print(type(logger._event_handler).__name__)", no_rotate=False)
    assert out == "RotatingFileHandler"


def test_receiver_follows_rotation_done_by_main_process(tmp_path):
    script = (
        "import os, logger\n"
        "logger.log_system('before_rotate')\n"
        "os.rename('events.jsonl', 'events.jsonl.1')  # what the main process does\n"
        "logger.log_system('after_rotate')\n"
    )
    _run_child(tmp_path, script, no_rotate=True)

    assert _events(tmp_path / "events.jsonl.1") == ["before_rotate"]
    assert _events(tmp_path / "events.jsonl") == ["after_rotate"]


def test_receiver_never_rotates_even_past_max_bytes(tmp_path):
    script = (
        "import logger\n"
        "for i in range(50):\n"
        "    logger.log_system('probe', {'i': i})\n"
    )
    _run_child(tmp_path, script, no_rotate=True, max_bytes=200)

    assert sorted(p.name for p in tmp_path.glob("events.jsonl*")) == ["events.jsonl"]
    assert len(_events(tmp_path / "events.jsonl")) == 50
