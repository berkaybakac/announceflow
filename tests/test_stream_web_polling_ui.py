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

