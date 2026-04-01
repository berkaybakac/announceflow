"""Critical packaging guards for agent stream_client module."""

from __future__ import annotations

import builtins
import importlib.util
from pathlib import Path


def test_stream_client_imports_without_repo_logger_dependency_critical(monkeypatch):
    """Critical: packaged agent must not hard-require root logger.py module."""
    module_path = Path(__file__).resolve().parents[1] / "agent" / "stream_client.py"
    assert module_path.exists()

    real_import = builtins.__import__

    def guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "logger" or name.startswith("logger."):
            raise ModuleNotFoundError("No module named 'logger'")
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", guarded_import)

    spec = importlib.util.spec_from_file_location(
        "stream_client_packaging_probe", str(module_path)
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert hasattr(module, "StreamClient")

