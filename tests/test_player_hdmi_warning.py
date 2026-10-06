"""Warn when music/announcements go to an HDMI card.

mpg123 happily plays into HDMI with no display attached: no error, no sound.
On Raspberry Pi OS trixie card 0 is HDMI and the jack is card 1, so a device
without the .env pin would be silent on site and nothing would say why.
"""
from unittest.mock import patch

import player as player_mod
from player import AudioPlayer

CARDS = (
    " 0 [vc4hdmi        ]: vc4-hdmi - vc4-hdmi\n"
    "                      vc4-hdmi\n"
    " 1 [Headphones     ]: bcm2835_headpho - bcm2835 Headphones\n"
    "                      bcm2835 Headphones\n"
)


def test_card_name_for_device_by_index_and_by_name():
    assert player_mod._alsa_card_name("plughw:0,0", CARDS) == "vc4hdmi"
    assert player_mod._alsa_card_name("plughw:1,0", CARDS) == "Headphones"
    assert player_mod._alsa_card_name("hw:Headphones,0", CARDS) == "Headphones"
    assert player_mod._alsa_card_name("plughw:9,0", CARDS) == ""
    assert player_mod._alsa_card_name("default", CARDS) == ""


def test_hdmi_selection_logged_once_per_device():
    calls = []
    with patch.object(player_mod, "log_error", lambda event, data: calls.append((event, data))), \
            patch.object(player_mod, "_read_asound_cards", lambda: CARDS):
        p = AudioPlayer()
        p._warn_if_hdmi_device("plughw:0,0")
        p._warn_if_hdmi_device("plughw:0,0")
        p._warn_if_hdmi_device("plughw:1,0")

    assert calls == [("audio_device_hdmi_selected", {"device": "plughw:0,0", "card": "vc4hdmi"})]
