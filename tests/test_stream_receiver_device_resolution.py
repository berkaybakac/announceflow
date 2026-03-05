"""Tests for ALSA device resolution in _stream_receiver.py."""
import sys
from types import SimpleNamespace

import _stream_receiver as receiver


def _completed(returncode=0, stdout="", stderr=""):
    return SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)


def _clear_audio_env(monkeypatch):
    monkeypatch.delenv("ANNOUNCEFLOW_ALSA_DEVICE", raising=False)
    monkeypatch.delenv("ANNOUNCEFLOW_ALSA_CARD", raising=False)


def test_cli_argument_takes_priority(monkeypatch):
    _clear_audio_env(monkeypatch)
    monkeypatch.setattr(
        sys, "argv", ["_stream_receiver.py", "5800", "plughw:9,0"]
    )
    assert receiver._resolve_alsa_device() == "plughw:9,0"


def test_env_device_takes_priority(monkeypatch):
    _clear_audio_env(monkeypatch)
    monkeypatch.setattr(sys, "argv", ["_stream_receiver.py", "5800"])
    monkeypatch.setenv("ANNOUNCEFLOW_ALSA_DEVICE", "plughw:7,0")
    assert receiver._resolve_alsa_device() == "plughw:7,0"


def test_env_card_converts_to_plughw(monkeypatch):
    _clear_audio_env(monkeypatch)
    monkeypatch.setattr(sys, "argv", ["_stream_receiver.py", "5800"])
    monkeypatch.setenv("ANNOUNCEFLOW_ALSA_CARD", "2")
    assert receiver._resolve_alsa_device() == "plughw:2,0"


def test_prefers_headphones_card_when_probe_succeeds(monkeypatch):
    _clear_audio_env(monkeypatch)
    monkeypatch.setattr(sys, "argv", ["_stream_receiver.py", "5800"])

    calls = []

    def fake_run(cmd, capture_output=False, text=False, timeout=None):
        calls.append(cmd)
        if cmd[:2] == ["aplay", "-l"]:
            return _completed(
                0,
                stdout=(
                    "card 0: vc4hdmi0 [vc4-hdmi-0], device 0: X [X]\n"
                    "card 2: Headphones [bcm2835 Headphones], device 0: Y [Y]\n"
                ),
            )
        if cmd and cmd[0] == "aplay" and "-D" in cmd:
            dev = cmd[cmd.index("-D") + 1]
            return _completed(0 if dev == "plughw:2,0" else 1)
        raise AssertionError(f"Unexpected subprocess command: {cmd}")

    monkeypatch.setattr(receiver.subprocess, "run", fake_run)
    chosen = receiver._resolve_alsa_device()
    assert chosen == "plughw:2,0"

    probe_calls = [c for c in calls if c and c[0] == "aplay" and "-D" in c]
    assert probe_calls, "Expected at least one probe call"
    first_probe = probe_calls[0]
    assert "-t" in first_probe and "raw" in first_probe
    assert "-f" in first_probe and "S16_LE" in first_probe
    assert "-r" in first_probe and "44100" in first_probe
    assert "-c" in first_probe and "1" in first_probe


def test_avoids_default_when_all_probes_fail(monkeypatch):
    _clear_audio_env(monkeypatch)
    monkeypatch.setattr(sys, "argv", ["_stream_receiver.py", "5800"])

    def fake_run(cmd, capture_output=False, text=False, timeout=None):
        if cmd[:2] == ["aplay", "-l"]:
            return _completed(
                0,
                stdout=(
                    "card 1: SomeCard [Some Card], device 0: Z [Z]\n"
                ),
            )
        if cmd and cmd[0] == "aplay" and "-D" in cmd:
            return _completed(1)
        raise AssertionError(f"Unexpected subprocess command: {cmd}")

    monkeypatch.setattr(receiver.subprocess, "run", fake_run)
    chosen = receiver._resolve_alsa_device()
    assert chosen != "default"
    assert chosen.startswith("plughw:")
