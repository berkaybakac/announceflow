"""UI volume (0-100) → Pi analog hardware volume calibration.

Field devices run at UI 25-37 (HW 82-86 %). UI 100 maps to HW 100 % = +4 dB
on the bcm2835 jack, which clips — so the curve itself is operationally
important and must not drift silently.
"""
import subprocess
from unittest.mock import patch

import pytest

import player as player_mod
from player import AudioPlayer


def _amixer_calls(volume: int, env_card: str = ""):
    calls = []

    def fake_run(argv, **kwargs):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, "", "")

    with patch.dict("os.environ", {"ANNOUNCEFLOW_ALSA_CARD": env_card}, clear=False), \
            patch.object(player_mod.platform, "system", return_value="Linux"), \
            patch.object(player_mod, "AUDIO_BACKEND", "mpg123"), \
            patch.object(player_mod.subprocess, "run", side_effect=fake_run):
        p = AudioPlayer()
        calls.clear()
        p.set_volume(volume)
    return p, [c for c in calls if c and c[0] == "amixer"]


@pytest.mark.parametrize(
    "ui, expected_args",
    [
        (0, ["mute"]),
        (9, ["mute"]),
        (10, ["70%", "unmute"]),
        (25, ["82%", "unmute"]),
        (37, ["86%", "unmute"]),
        (50, ["90%", "unmute"]),
        (80, ["96%", "unmute"]),
        (100, ["100%", "unmute"]),
    ],
)
def test_ui_volume_maps_to_hardware_curve(ui, expected_args):
    p, calls = _amixer_calls(ui)

    assert p.get_volume() == ui
    assert calls, "set_volume must drive amixer on Linux"
    assert calls[0][-len(expected_args):] == expected_args
    assert calls[0][3:5] == ["set", "PCM"]


def test_volume_is_clamped_to_0_100():
    p, calls = _amixer_calls(150)
    assert p.get_volume() == 100
    assert calls[0][-2:] == ["100%", "unmute"]

    p, calls = _amixer_calls(-5)
    assert p.get_volume() == 0
    assert calls[0][-1:] == ["mute"]


def test_env_card_is_tried_first():
    """Field devices pin the jack card via .env (e.g. Pi 3B+ trixie: card 1)."""
    _, calls = _amixer_calls(37, env_card="1")
    assert calls[0][1:3] == ["-c", "1"]
