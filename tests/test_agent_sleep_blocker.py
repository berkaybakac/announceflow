"""Tests for _SleepBlocker (Windows idle-sleep prevention during streaming).

Runs stream_client.py in a subprocess, matching test_stream_client_config.py:
in-process sys.path insertion breaks agent.stream_client's namespace-package
import used elsewhere (test_stream_skeleton.py), since agent/ has no __init__.py.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
AGENT_DIR = ROOT / "agent"

# Fakes ctypes.windll to exercise the Windows-only path on any OS.
_HARNESS = """
import sys, types, json
sys.path.insert(0, {agent_dir!r})

calls = []

def _fake_set_thread_execution_state(flags):
    calls.append(flags)
    return {return_value}

kernel32 = types.SimpleNamespace(SetThreadExecutionState=_fake_set_thread_execution_state)
windll = types.SimpleNamespace(kernel32=kernel32)
sys.modules["ctypes"] = types.SimpleNamespace(windll=windll)

import stream_client
stream_client.os.name = {os_name!r}

blocker = stream_client._SleepBlocker()
{actions}

print(json.dumps({{"calls": calls, "active": blocker._active}}))
"""


def _run(os_name: str, actions: str, return_value: int = 1, env_extra: dict | None = None) -> dict:
    script = _HARNESS.format(
        agent_dir=str(AGENT_DIR), os_name=os_name, actions=actions, return_value=return_value
    )
    env = os.environ.copy()
    env.pop("ANNOUNCEFLOW_AGENT_PREVENT_SLEEP", None)
    if env_extra:
        env.update(env_extra)
    proc = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout.strip())


def test_noop_on_non_windows():
    result = _run(os_name="posix", actions="blocker.acquire()")
    assert result == {"calls": [], "active": False}


def test_disabled_via_env_var():
    result = _run(
        os_name="nt",
        actions="blocker.acquire()",
        env_extra={"ANNOUNCEFLOW_AGENT_PREVENT_SLEEP": "0"},
    )
    assert result == {"calls": [], "active": False}


def test_acquire_sets_continuous_and_system_required():
    result = _run(os_name="nt", actions="blocker.acquire()")
    assert result["active"] is True
    assert result["calls"] == [0x80000000 | 0x00000001]


def test_acquire_is_idempotent():
    result = _run(os_name="nt", actions="blocker.acquire()\nblocker.acquire()")
    assert len(result["calls"]) == 1


def test_release_restores_continuous_only():
    result = _run(os_name="nt", actions="blocker.acquire()\nblocker.release()")
    assert result["active"] is False
    assert result["calls"][-1] == 0x80000000


def test_release_noop_when_never_acquired():
    result = _run(os_name="nt", actions="blocker.release()")
    assert result == {"calls": [], "active": False}


def test_acquire_handles_os_rejection():
    result = _run(os_name="nt", actions="blocker.acquire()", return_value=0)
    assert result["active"] is False
