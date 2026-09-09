"""Tests for _apply_receiver_priority: reversible niceness boost, on by
default (validated 2026-09), opt-out via env var.

See docs/backlog.md P0 (common xrun validation, staged priority test).
"""
from __future__ import annotations

import _stream_receiver as receiver


def test_enabled_by_default(monkeypatch):
    monkeypatch.delenv("ANNOUNCEFLOW_STREAM_RECEIVER_NICE", raising=False)
    called = []
    monkeypatch.setattr(receiver.os, "nice", lambda v: called.append(v) or v)

    receiver._apply_receiver_priority()

    assert called == [receiver._DEFAULT_RECEIVER_NICE]


def test_zero_opts_out(monkeypatch):
    monkeypatch.setenv("ANNOUNCEFLOW_STREAM_RECEIVER_NICE", "0")
    called = []
    monkeypatch.setattr(receiver.os, "nice", lambda v: called.append(v) or v)

    receiver._apply_receiver_priority()

    assert called == []


def test_applies_configured_delta(monkeypatch):
    monkeypatch.setenv("ANNOUNCEFLOW_STREAM_RECEIVER_NICE", "-10")
    events = []
    monkeypatch.setattr(receiver.os, "nice", lambda v: -10)
    monkeypatch.setattr(
        receiver, "_safe_log_system", lambda event, data: events.append((event, data))
    )

    receiver._apply_receiver_priority()

    assert events == [
        ("stream_receiver_priority_applied", {"requested_delta": -10, "resulting_nice": -10})
    ]


def test_oserror_is_non_fatal_and_logged(monkeypatch):
    """Missing CAP_SYS_NICE must not crash the receiver."""
    monkeypatch.setenv("ANNOUNCEFLOW_STREAM_RECEIVER_NICE", "-10")
    events = []

    def _raise(_value):
        raise OSError("Operation not permitted")

    monkeypatch.setattr(receiver.os, "nice", _raise)
    monkeypatch.setattr(
        receiver, "_safe_log_system", lambda event, data: events.append((event, data))
    )

    receiver._apply_receiver_priority()  # must not raise

    assert events[0][0] == "stream_receiver_priority_failed"
    assert events[0][1]["requested_delta"] == -10


def test_invalid_value_is_ignored(monkeypatch):
    monkeypatch.setenv("ANNOUNCEFLOW_STREAM_RECEIVER_NICE", "not-a-number")
    called = []
    monkeypatch.setattr(receiver.os, "nice", lambda v: called.append(v) or v)

    receiver._apply_receiver_priority()  # must not raise

    assert called == []
