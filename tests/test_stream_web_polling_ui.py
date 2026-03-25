"""Web stream polling regressions for cross-client UI sync."""
import re
from pathlib import Path


_INDEX_TEMPLATE = (
    Path(__file__).resolve().parent.parent / "templates" / "index.html"
)


def _source() -> str:
    return _INDEX_TEMPLATE.read_text(encoding="utf-8")


def _function_snippet(name: str) -> str:
    source = _source()
    start = source.index(f"async function {name}(")
    end = source.find("\n    async function ", start + 1)
    if end == -1:
        end = source.find("\n    document.addEventListener", start + 1)
    if end == -1:
        end = len(source)
    return source[start:end]


class TestStreamWebPolling:
    def test_poll_interval_is_three_seconds_or_less(self):
        match = re.search(
            r"const\s+STREAM_POLL_INTERVAL_MS\s*=\s*(\d+)\s*;",
            _source(),
        )
        assert match, "STREAM_POLL_INTERVAL_MS constant not found"
        assert int(match.group(1)) <= 3000

    def test_run_stream_poll_rearms_in_finally(self):
        snippet = _function_snippet("runStreamPoll")
        assert "try {" in snippet
        assert "finally {" in snippet
        assert "scheduleNextStreamPoll();" in snippet

    def test_start_and_stop_actions_still_force_state_refresh(self):
        assert "await updateStreamState();" in _function_snippet("streamStart")
        assert "await updateStreamState();" in _function_snippet("streamStop")

    def test_volume_mute_button_exists(self):
        source = _source()
        assert 'id="volumeMuteBtn"' in source
        assert "onclick=\"toggleMute()\"" in source

    def test_set_volume_updates_mute_button(self):
        snippet = _function_snippet("setVolume")
        assert "sendVolumeIntent({ volume: nextVolume }, optimisticState)" in snippet

    def test_toggle_mute_restores_last_nonzero_volume(self):
        snippet = _function_snippet("toggleMute")
        assert "await setMuted(!isMuted);" in snippet

    def test_apply_volume_state_has_inflight_and_revision_guards(self):
        source = _source()
        assert "if (volumeWriteInFlight)" in source
        assert "volume_revision < lastAppliedVolumeRevision" in source

    def test_apply_volume_state_supports_effective_output_fields(self):
        source = _source()
        assert "effective_volume" in source
        assert "effective_muted" in source
        assert "mute_override_active" in source

    def test_send_volume_intent_shows_toast_on_network_error(self):
        snippet = _function_snippet("sendVolumeIntent")
        assert "showToast('Ses ayarı uygulanamadı. Tekrar deneyin.', 'error');" in snippet
