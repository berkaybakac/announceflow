"""Lifecycle regressions for stream receiver module wiring."""

from __future__ import annotations

import _stream_receiver as receiver


def test_stream_receiver_signal_module_is_available_critical():
    """Critical: receiver main() registers signal handlers, import must exist."""
    assert hasattr(receiver, "signal")
    assert hasattr(receiver.signal, "SIGTERM")
    assert callable(receiver.signal.signal)

