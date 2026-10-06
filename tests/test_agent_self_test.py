"""`StatekSound.exe --self-test <file>`: catches a broken PyInstaller bundle.

soundcard and numpy are imported only when a stream starts, so a module
missing from the EXE would only surface at a customer's first stream. CI
runs the built EXE in this mode and fails unless the file says PASS.
"""
import os
import sys
from unittest.mock import MagicMock

import pytest

_agent_dir = os.path.join(os.path.dirname(__file__), "..", "agent")
_tk_modules = {
    "tkinter": MagicMock(),
    "tkinter.ttk": MagicMock(),
    "tkinter.filedialog": MagicMock(),
    "tkinter.messagebox": MagicMock(),
}

try:
    import tkinter  # noqa: F401
    _need_tk_mock = False
except ImportError:
    _need_tk_mock = True


@pytest.fixture
def agent_mod(monkeypatch):
    sys.path.insert(0, _agent_dir)
    if _need_tk_mock:
        for name, mock in _tk_modules.items():
            monkeypatch.setitem(sys.modules, name, mock)
    import agent as mod
    yield mod
    for key in list(sys.modules):
        if key == "agent" or key.startswith("agent."):
            del sys.modules[key]
    while _agent_dir in sys.path:
        sys.path.remove(_agent_dir)


def test_self_test_passes_when_all_modules_import(agent_mod, tmp_path):
    out = tmp_path / "selftest.txt"
    code = agent_mod.run_self_test(str(out), import_module=lambda name: object())

    text = out.read_text(encoding="utf-8")
    assert code == 0
    assert f"version={agent_mod.AGENT_VERSION}" in text
    assert "soundcard: ok" in text and "numpy: ok" in text
    assert text.strip().endswith("RESULT=PASS")


def test_self_test_fails_and_names_the_missing_module(agent_mod, tmp_path):
    def import_module(name):
        if name == "soundcard":
            raise ImportError("No module named 'soundcard'")
        return object()

    out = tmp_path / "selftest.txt"
    code = agent_mod.run_self_test(str(out), import_module=import_module)

    text = out.read_text(encoding="utf-8")
    assert code == 1
    assert "soundcard: error: ImportError: No module named 'soundcard'" in text
    assert text.strip().endswith("RESULT=FAIL")


def test_self_test_argument_parsing(agent_mod):
    assert agent_mod._self_test_output_path(["StatekSound.exe", "--self-test", "C:\\t\\out.txt"]) == "C:\\t\\out.txt"
    assert agent_mod._self_test_output_path(["StatekSound.exe"]) is None
    assert agent_mod._self_test_output_path(["StatekSound.exe", "--self-test"]) == "selftest.txt"
