"""setup_logging() must emit UTC timestamps, matching events.jsonl (logger.py)."""
from __future__ import annotations

import logging
import time

import main as main_mod


def test_setup_logging_uses_utc_converter(tmp_path, monkeypatch):
    log_file = tmp_path / "test.log"
    monkeypatch.setenv("ANNOUNCEFLOW_APP_LOG_FILE", str(log_file))

    logger = main_mod.setup_logging()

    handler = next(h for h in logger.handlers if isinstance(h, logging.FileHandler))
    assert handler.formatter.converter is time.gmtime
