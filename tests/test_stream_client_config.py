"""Config parsing tests for StreamClient module-level settings."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
AGENT_DIR = ROOT / "agent"


def _read_block_size(env_value: str | None) -> int:
    env = os.environ.copy()
    if env_value is None:
        env.pop("ANNOUNCEFLOW_STREAM_BLOCK_SIZE", None)
    else:
        env["ANNOUNCEFLOW_STREAM_BLOCK_SIZE"] = env_value
    proc = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import sys; "
                f"sys.path.insert(0, {str(AGENT_DIR)!r}); "
                "import stream_client; "
                "print(stream_client._BLOCK_SIZE)"
            ),
        ],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    return int(proc.stdout.strip())


def test_block_size_defaults_when_env_missing():
    assert _read_block_size(None) == 735


def test_block_size_defaults_when_env_invalid():
    assert _read_block_size("not-an-int") == 735


def test_block_size_clamped_to_minimum():
    assert _read_block_size("10") == 220


def test_block_size_clamped_to_maximum():
    assert _read_block_size("999999") == 8820
