"""Agent GUI refactor tests — status bar, loading state, theme, layout.

Validates the V2 GUI changes:
- Pop-ups replaced with status bar
- Buttons grouped (Music / Stream / Tools)
- Loading state on buttons
- Theme colors softened
- Logout moved to header
- No messagebox usage
- print() replaced with logger
"""
import os
import sys
from unittest.mock import MagicMock, patch

import pytest

# --------------- tkinter mock setup ---------------

_tk_mock = MagicMock()
_tkinter_modules = {
    "tkinter": _tk_mock,
    "tkinter.ttk": MagicMock(),
    "tkinter.filedialog": MagicMock(),
    "tkinter.messagebox": MagicMock(),
}

try:
    import tkinter  # noqa: F401
    _need_tk_mock = False
except ImportError:
    _need_tk_mock = True

_agent_dir = os.path.join(os.path.dirname(__file__), "..", "agent")


@pytest.fixture(autouse=True)
def _mock_tkinter(monkeypatch):
    sys.path.insert(0, _agent_dir)
    if _need_tk_mock:
        for mod_name, mock_obj in _tkinter_modules.items():
            monkeypatch.setitem(sys.modules, mod_name, mock_obj)
    yield
    for key in list(sys.modules.keys()):
        if key == "agent" or key.startswith("agent."):
            del sys.modules[key]
    while _agent_dir in sys.path:
        sys.path.remove(_agent_dir)


def _import_agent():
    if _need_tk_mock:
        for mod_name, mock_obj in _tkinter_modules.items():
            sys.modules.setdefault(mod_name, mock_obj)
    import agent as agent_mod
    return agent_mod


def _make_gui():
    """Create a minimal AgentGUI with mocked deps."""
    agent_mod = _import_agent()
    AgentGUI = agent_mod.AgentGUI

    agent_mock = MagicMock()
    agent_mock.api_base = "http://aflow.local:5001"
    agent_mock.config = {"api_base": "http://aflow.local:5001"}

    gui = AgentGUI.__new__(AgentGUI)
    gui.agent = agent_mock
    gui.root = MagicMock()
    gui.root.winfo_exists.return_value = True
    gui.logged_in = False
    gui.network_worker = MagicMock()
    gui._closing = False
    gui._volume_update_job = None
    gui._pending_volume = None
    gui._status_clear_job = None
    gui._btn_music_start = None
    gui._btn_music_stop = None
    gui._btn_stream_start = None
    gui._btn_stream_stop = None
    gui._btn_upload = None
    gui._stream_active = False
    gui._heartbeat_job = None
    gui._poll_job = None

    from stream_client import StreamClient
    gui._stream_client = MagicMock(spec=StreamClient)

    return gui


# ==================== 1. No messagebox import ====================


class TestNoMessagebox:
    def test_no_messagebox_import(self):
        """agent.py should not import messagebox (pop-ups removed)."""
        source_path = os.path.join(_agent_dir, "agent.py")
        with open(source_path) as f:
            source = f.read()
        # Should not have messagebox in import line
        assert "messagebox" not in source

    def test_no_messagebox_calls_in_actions(self):
        """No messagebox.showinfo/showerror calls in action methods."""
        source_path = os.path.join(_agent_dir, "agent.py")
        with open(source_path) as f:
            source = f.read()
        assert "messagebox.showinfo" not in source
        assert "messagebox.showerror" not in source


# ==================== 2. No print() calls ====================


class TestNoPrints:
    def test_no_print_calls(self):
        """agent.py should use logger instead of print()."""
        source_path = os.path.join(_agent_dir, "agent.py")
        with open(source_path) as f:
            lines = f.readlines()
        for i, line in enumerate(lines, 1):
            stripped = line.strip()
            # Skip comments and strings
            if stripped.startswith("#"):
                continue
            if stripped.startswith("print("):
                pytest.fail(f"Found print() call at line {i}: {stripped}")


# ==================== 3. Theme constants ====================


class TestThemeColors:
    def test_theme_constants_exist(self):
        """Theme color constants should be defined."""
        agent_mod = _import_agent()
        assert hasattr(agent_mod, "_BG")
        assert hasattr(agent_mod, "_BG_HEADER")
        assert hasattr(agent_mod, "_BG_CARD")
        assert hasattr(agent_mod, "_FG")
        assert hasattr(agent_mod, "_FG_DIM")

    def test_bg_not_pure_black(self):
        """Background should not be pure black (#000000 or #1a1a1a)."""
        agent_mod = _import_agent()
        assert agent_mod._BG != "#000000"
        assert agent_mod._BG != "#1a1a1a"

    def test_status_colors_exist(self):
        """Status bar colors should be defined."""
        agent_mod = _import_agent()
        assert hasattr(agent_mod, "_STATUS_SUCCESS")
        assert hasattr(agent_mod, "_STATUS_ERROR")


# ==================== 4. Button grouping ====================


class TestButtonGrouping:
    def test_button_groups_in_source(self):
        """Main frame should have grouped sections: Music, Stream, Tools."""
        source_path = os.path.join(_agent_dir, "agent.py")
        with open(source_path) as f:
            source = f.read()
        assert "Müzik Kontrolü" in source
        assert "Canlı Yayın" in source
        assert "Araçlar" in source

    def test_buttons_are_side_by_side(self):
        """Buttons within groups use side='left' packing (side by side)."""
        source_path = os.path.join(_agent_dir, "agent.py")
        with open(source_path) as f:
            source = f.read()
        # Music buttons packed side by side
        assert '_btn_music_start' in source
        assert '_btn_music_stop' in source
        assert '_btn_stream_start' in source
        assert '_btn_stream_stop' in source


# ==================== 5. Status bar (replaces pop-ups) ====================


class TestStatusBar:
    def test_show_status_method_exists(self):
        """AgentGUI should have _show_status method."""
        gui = _make_gui()
        assert hasattr(gui, "_show_status")
        assert callable(gui._show_status)

    def test_show_status_success(self):
        """_show_status with error=False uses success color."""
        gui = _make_gui()
        gui._status_label = MagicMock()
        gui._status_label.winfo_exists.return_value = True

        gui._show_status("Müzik başlatıldı")

        gui._status_label.config.assert_called()
        call_kwargs = gui._status_label.config.call_args
        assert "Müzik başlatıldı" in str(call_kwargs)

    def test_show_status_error(self):
        """_show_status with error=True uses error color."""
        agent_mod = _import_agent()
        gui = _make_gui()
        gui._status_label = MagicMock()
        gui._status_label.winfo_exists.return_value = True

        gui._show_status("Bağlantı hatası", error=True)

        call_kwargs = gui._status_label.config.call_args
        assert agent_mod._STATUS_ERROR in str(call_kwargs)

    def test_show_status_schedules_auto_clear(self):
        """_show_status schedules an auto-clear via root.after."""
        gui = _make_gui()
        gui._status_label = MagicMock()
        gui._status_label.winfo_exists.return_value = True

        gui._show_status("Test message")

        gui.root.after.assert_called()

    def test_show_status_cancels_previous_timer(self):
        """Showing a new status cancels the previous auto-clear timer."""
        gui = _make_gui()
        gui._status_label = MagicMock()
        gui._status_label.winfo_exists.return_value = True
        gui._status_clear_job = "previous_job_id"

        gui._show_status("New message")

        gui.root.after_cancel.assert_called_with("previous_job_id")

    def test_clear_status(self):
        """_clear_status sets label text to empty."""
        gui = _make_gui()
        gui._status_label = MagicMock()
        gui._status_label.winfo_exists.return_value = True

        gui._clear_status()

        gui._status_label.config.assert_called_with(text="")

    def test_show_status_noop_when_closing(self):
        """_show_status does nothing when app is closing."""
        gui = _make_gui()
        gui._closing = True
        gui._status_label = MagicMock()

        gui._show_status("Should not appear")

        gui._status_label.config.assert_not_called()


# ==================== 6. Loading state ====================


class TestLoadingState:
    def test_with_loading_returns_restore_function(self):
        """_with_loading should return a callable restore function."""
        gui = _make_gui()
        restore = gui._with_loading(None, "Test")
        assert callable(restore)

    def test_with_loading_disables_button(self):
        """_with_loading should disable the button and change text."""
        gui = _make_gui()
        mock_btn = MagicMock()

        gui._with_loading(mock_btn, "Başlat", "Başlatılıyor...")

        mock_btn.set_disabled.assert_called_with(True)
        mock_btn.set_text.assert_called_with("Başlatılıyor...")

    def test_restore_re_enables_button(self):
        """Calling restore should re-enable the button and restore text."""
        gui = _make_gui()
        mock_btn = MagicMock()
        mock_btn.winfo_exists.return_value = True

        restore = gui._with_loading(mock_btn, "Başlat", "Başlatılıyor...")
        restore()

        # Last calls should be re-enable
        mock_btn.set_disabled.assert_called_with(False)
        mock_btn.set_text.assert_called_with("Başlat")

    def test_with_loading_none_button(self):
        """_with_loading with None button should not crash."""
        gui = _make_gui()
        restore = gui._with_loading(None, "Test")
        restore()  # Should not raise

    def test_music_start_uses_loading(self):
        """start_music should use loading state."""
        gui = _make_gui()
        gui._btn_music_start = MagicMock()

        captured = {}

        def fake_submit(fn, *, on_success=None, on_error=None):
            captured["fn"] = fn
            captured["on_success"] = on_success

        gui._submit_network_job = fake_submit
        gui.start_music()

        # Button should be disabled during loading
        gui._btn_music_start.set_disabled.assert_called_with(True)
        gui._btn_music_start.set_text.assert_called_with("Başlatılıyor...")

    def test_stream_start_uses_loading(self):
        """start_stream should use loading state."""
        gui = _make_gui()
        gui._btn_stream_start = MagicMock()

        captured = {}

        def fake_submit(fn, *, on_success=None, on_error=None):
            captured["on_success"] = on_success

        gui._submit_network_job = fake_submit
        gui.start_stream()

        gui._btn_stream_start.set_disabled.assert_called_with(True)
        gui._btn_stream_start.set_text.assert_called_with("Bağlanıyor...")


# ==================== 7. Logout in header ====================


class TestLogoutInHeader:
    def test_no_logout_modern_button(self):
        """Logout should NOT be a big ModernButton anymore."""
        source_path = os.path.join(_agent_dir, "agent.py")
        with open(source_path) as f:
            source = f.read()
        # Old pattern: ModernButton(..., text="Çıkış Yap", ...)
        assert 'text="Çıkış Yap"' not in source

    def test_logout_is_text_link(self):
        """Logout should be a small text label in the header."""
        source_path = os.path.join(_agent_dir, "agent.py")
        with open(source_path) as f:
            source = f.read()
        assert 'text="Çıkış"' in source
        assert "underline" in source


# ==================== 8. Window size ====================


class TestWindowSize:
    def test_geometry_larger_than_before(self):
        """Window geometry should be at least 420x760."""
        source_path = os.path.join(_agent_dir, "agent.py")
        with open(source_path) as f:
            source = f.read()
        assert "420x760" in source

    def test_minsize_set(self):
        """minsize should be set to prevent content clipping."""
        source_path = os.path.join(_agent_dir, "agent.py")
        with open(source_path) as f:
            source = f.read()
        assert "minsize" in source


# ==================== 9. ModernButton enhancements ====================


class TestModernButtonEnhancements:
    def test_set_disabled_method(self):
        """ModernButton should have set_disabled method."""
        agent_mod = _import_agent()
        source_path = os.path.join(_agent_dir, "agent.py")
        with open(source_path) as f:
            source = f.read()
        assert "def set_disabled" in source

    def test_set_text_method(self):
        """ModernButton should have set_text method."""
        source_path = os.path.join(_agent_dir, "agent.py")
        with open(source_path) as f:
            source = f.read()
        assert "def set_text" in source


# ==================== 10. Action callbacks use status bar ====================


class TestActionCallbacksUseStatusBar:
    def test_start_music_success_shows_status(self):
        """start_music success should call _show_status, not messagebox."""
        gui = _make_gui()
        gui._btn_music_start = MagicMock()
        gui._btn_music_start.winfo_exists.return_value = True
        gui._status_label = MagicMock()
        gui._status_label.winfo_exists.return_value = True

        captured = {}

        def fake_submit(fn, *, on_success=None, on_error=None):
            captured["on_success"] = on_success

        gui._submit_network_job = fake_submit
        gui.start_music()

        # Simulate success callback
        captured["on_success"](True)

        # Status label should be updated (not messagebox)
        gui._status_label.config.assert_called()
        assert "başlatıldı" in str(gui._status_label.config.call_args).lower()

    def test_stop_music_success_shows_status(self):
        """stop_music success should call _show_status."""
        gui = _make_gui()
        gui._btn_music_stop = MagicMock()
        gui._btn_music_stop.winfo_exists.return_value = True
        gui._status_label = MagicMock()
        gui._status_label.winfo_exists.return_value = True

        captured = {}

        def fake_submit(fn, *, on_success=None, on_error=None):
            captured["on_success"] = on_success

        gui._submit_network_job = fake_submit
        gui.stop_music()

        captured["on_success"](True)

        gui._status_label.config.assert_called()
        assert "durduruldu" in str(gui._status_label.config.call_args).lower()

    def test_stream_start_success_shows_status(self):
        """start_stream success should use status bar."""
        gui = _make_gui()
        gui._btn_stream_start = MagicMock()
        gui._btn_stream_start.winfo_exists.return_value = True
        gui._status_label = MagicMock()
        gui._status_label.winfo_exists.return_value = True

        captured = {}

        def fake_submit(fn, *, on_success=None, on_error=None):
            captured["on_success"] = on_success

        gui._submit_network_job = fake_submit
        gui.start_stream()

        captured["on_success"]("ok")

        gui._status_label.config.assert_called()
        assert "yayın" in str(gui._status_label.config.call_args).lower()

    def test_stream_start_error_shows_status(self):
        """start_stream failure should show error in status bar."""
        gui = _make_gui()
        gui._btn_stream_start = MagicMock()
        gui._btn_stream_start.winfo_exists.return_value = True
        gui._status_label = MagicMock()
        gui._status_label.winfo_exists.return_value = True

        captured = {}

        def fake_submit(fn, *, on_success=None, on_error=None):
            captured["on_success"] = on_success

        gui._submit_network_job = fake_submit
        gui.start_stream()

        captured["on_success"]("api_fail")

        gui._status_label.config.assert_called()
        call_str = str(gui._status_label.config.call_args)
        # Should show error color
        agent_mod = _import_agent()
        assert agent_mod._STATUS_ERROR in call_str
