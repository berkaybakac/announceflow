"""Agent login flow tests for host-first + safe discovery behavior."""
import os
import sys
from unittest.mock import MagicMock

import pytest


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


def _make_gui(agent_mod):
    AgentGUI = agent_mod.AgentGUI

    gui = AgentGUI.__new__(AgentGUI)
    gui._advanced_visible = False
    gui._stream_poll_active = False
    gui.logged_in = False
    gui._closing = False
    gui.network_worker = MagicMock()
    gui.root = MagicMock()
    gui.root.winfo_exists.return_value = True
    gui._run_status_poll = MagicMock()
    gui.show_main_frame = MagicMock()
    gui._show_status = MagicMock()

    def _toggle():
        gui._advanced_visible = not gui._advanced_visible

    gui._toggle_advanced = MagicMock(side_effect=_toggle)
    gui._root_alive = lambda: True

    gui.url_entry = MagicMock()
    gui.url_entry.get.return_value = "http://rpi001.local:5001"
    gui.url_entry.winfo_exists.return_value = True
    gui.username_entry = MagicMock()
    gui.username_entry.get.return_value = "admin"
    gui.password_entry = MagicMock()
    gui.password_entry.get.return_value = "secret"
    gui.remember_var = MagicMock()
    gui.remember_var.get.return_value = True
    gui.status_label = MagicMock()

    agent = MagicMock()
    agent.api_base = "http://rpi001.local:5001"
    agent.config = {"api_base": "http://rpi001.local:5001"}
    agent.get_expected_identity.return_value = {
        "instance_id": "inst-expected",
        "site_name": "Site Expected",
    }
    agent.get_cached_ip_url.return_value = None
    agent.discover_server.return_value = None
    agent.get_health.return_value = {
        "identity": {"instance_id": "inst-expected", "site_name": "Site Expected"}
    }
    gui.agent = agent

    def run_sync(fn, *, on_success=None, on_error=None):
        try:
            result = fn()
        except Exception as exc:  # pragma: no cover
            if on_error:
                on_error(exc)
            else:
                raise
            return
        if on_success:
            on_success(result)

    gui._submit_network_job = run_sync
    return gui


def test_login_success_host_keeps_host_profile(monkeypatch):
    agent_mod = _import_agent()
    gui = _make_gui(agent_mod)
    gui.agent.login.return_value = {"success": True}
    save_credentials = MagicMock(return_value=True)
    delete_credentials = MagicMock(return_value=True)

    monkeypatch.setattr(agent_mod, "save_agent_config", lambda cfg: True)
    monkeypatch.setattr(agent_mod, "save_credentials", save_credentials)
    monkeypatch.setattr(agent_mod, "delete_credentials", delete_credentials)

    gui.do_login()

    assert gui.logged_in is True
    assert gui.agent.api_base == "http://rpi001.local:5001"
    gui.agent.remember_successful_connection.assert_called_once()
    save_credentials.assert_called_once_with(
        "http://rpi001.local:5001", "admin", "secret"
    )
    delete_credentials.assert_not_called()
    gui.agent.discover_server.assert_not_called()


def test_login_success_ip_keeps_ip_profile(monkeypatch):
    agent_mod = _import_agent()
    gui = _make_gui(agent_mod)
    gui.url_entry.get.return_value = "http://192.168.1.55:5001"
    gui.agent.login.return_value = {"success": True}
    save_credentials = MagicMock(return_value=True)

    monkeypatch.setattr(agent_mod, "save_agent_config", lambda cfg: True)
    monkeypatch.setattr(agent_mod, "save_credentials", save_credentials)
    monkeypatch.setattr(agent_mod, "delete_credentials", lambda u: True)

    gui.do_login()

    assert gui.logged_in is True
    assert gui.agent.api_base == "http://192.168.1.55:5001"
    save_credentials.assert_called_once_with(
        "http://192.168.1.55:5001", "admin", "secret"
    )


def test_login_connection_error_uses_cached_ip_then_keeps_host(monkeypatch):
    agent_mod = _import_agent()
    gui = _make_gui(agent_mod)
    gui.agent.login.side_effect = [
        {"success": False, "error": "connection_error"},
        {"success": True},
    ]
    gui.agent.get_cached_ip_url.return_value = "http://192.168.1.99:5001"
    save_credentials = MagicMock(return_value=True)

    monkeypatch.setattr(agent_mod, "save_agent_config", lambda cfg: True)
    monkeypatch.setattr(agent_mod, "save_credentials", save_credentials)
    monkeypatch.setattr(agent_mod, "delete_credentials", lambda u: True)

    gui.do_login()

    assert gui.logged_in is True
    assert gui.agent.login.call_count == 2
    assert gui.agent.api_base == "http://rpi001.local:5001"
    gui.agent.remember_successful_connection.assert_called_once_with(
        configured_url="http://rpi001.local:5001",
        resolved_url="http://192.168.1.99:5001",
        identity={"instance_id": "inst-expected", "site_name": "Site Expected"},
    )
    gui._show_status.assert_called_once()
    assert "önbellek IP" in str(gui._show_status.call_args)
    save_credentials.assert_called_once_with(
        "http://rpi001.local:5001", "admin", "secret"
    )


def test_login_discovery_requires_explicit_confirmation(monkeypatch):
    agent_mod = _import_agent()
    gui = _make_gui(agent_mod)
    gui.agent.login.return_value = {"success": False, "error": "connection_error"}
    gui.agent.discover_server.return_value = {
        "url": "http://192.168.1.77:5001",
        "site_name": "Site X",
        "instance_id": "inst-x-1234",
    }
    save_credentials = MagicMock(return_value=True)

    monkeypatch.setattr(agent_mod, "save_agent_config", lambda cfg: True)
    monkeypatch.setattr(agent_mod, "save_credentials", save_credentials)
    monkeypatch.setattr(agent_mod, "delete_credentials", lambda u: True)

    gui.do_login()

    assert gui.logged_in is False
    assert gui.agent.login.call_count == 1
    assert gui._toggle_advanced.call_count == 1
    gui.url_entry.insert.assert_called_with(0, "http://192.168.1.77:5001")
    gui.status_label.config.assert_called()
    assert "Onay için Giriş Yap'a tekrar basın." in str(gui.status_label.config.call_args)
    save_credentials.assert_not_called()


def test_login_connection_error_without_discovery_shows_network_error(monkeypatch):
    agent_mod = _import_agent()
    gui = _make_gui(agent_mod)
    gui.agent.login.return_value = {"success": False, "error": "connection_error"}
    gui.agent.discover_server.return_value = None

    monkeypatch.setattr(agent_mod, "save_agent_config", lambda cfg: True)
    monkeypatch.setattr(agent_mod, "save_credentials", lambda *a, **k: True)
    monkeypatch.setattr(agent_mod, "delete_credentials", lambda *a, **k: True)

    gui.do_login()

    assert gui.logged_in is False
    gui.status_label.config.assert_called_with(text="Sunucu ağda bulunamadı!", fg=agent_mod._RED)


def test_login_invalid_credentials_stops_without_fallback(monkeypatch):
    agent_mod = _import_agent()
    gui = _make_gui(agent_mod)
    gui.agent.login.return_value = {"success": False, "error": "invalid_credentials"}

    monkeypatch.setattr(agent_mod, "save_agent_config", lambda cfg: True)

    gui.do_login()

    gui.agent.get_cached_ip_url.assert_not_called()
    gui.agent.discover_server.assert_not_called()
    gui.status_label.config.assert_called_with(
        text="Giriş başarısız! Bilgileri kontrol edin.", fg=agent_mod._RED
    )


def test_login_timeout_message(monkeypatch):
    agent_mod = _import_agent()
    gui = _make_gui(agent_mod)
    gui.agent.login.return_value = {"success": False, "error": "timeout"}
    monkeypatch.setattr(agent_mod, "save_agent_config", lambda cfg: True)

    gui.do_login()

    gui.status_label.config.assert_called_with(
        text="Bağlantı zaman aşımına uğradı!", fg=agent_mod._RED
    )


def test_login_passes_expected_identity_to_discovery(monkeypatch):
    agent_mod = _import_agent()
    gui = _make_gui(agent_mod)
    gui.agent.login.return_value = {"success": False, "error": "connection_error"}
    gui.agent.discover_server.return_value = None
    gui.agent.get_expected_identity.return_value = {
        "instance_id": "inst-777",
        "site_name": "Site-777",
    }

    monkeypatch.setattr(agent_mod, "save_agent_config", lambda cfg: True)

    gui.do_login()

    gui.agent.discover_server.assert_called_once_with(
        expected_instance_id="inst-777", expected_site_name="Site-777"
    )


def test_login_when_remember_disabled_deletes_credentials(monkeypatch):
    agent_mod = _import_agent()
    gui = _make_gui(agent_mod)
    gui.agent.login.return_value = {"success": True}
    gui.remember_var.get.return_value = False
    delete_credentials = MagicMock(return_value=True)
    save_credentials = MagicMock(return_value=True)

    monkeypatch.setattr(agent_mod, "save_agent_config", lambda cfg: True)
    monkeypatch.setattr(agent_mod, "delete_credentials", delete_credentials)
    monkeypatch.setattr(agent_mod, "save_credentials", save_credentials)

    gui.do_login()

    delete_credentials.assert_called_once_with("http://rpi001.local:5001")
    save_credentials.assert_not_called()


def test_login_empty_url_falls_back_to_current_api_base(monkeypatch):
    agent_mod = _import_agent()
    gui = _make_gui(agent_mod)
    gui.url_entry.get.return_value = ""
    gui.agent.api_base = "http://current.local:5001"
    gui.agent.config = {"api_base": "http://current.local:5001"}
    gui.agent.login.return_value = {"success": True}

    monkeypatch.setattr(agent_mod, "save_agent_config", lambda cfg: True)
    monkeypatch.setattr(agent_mod, "save_credentials", lambda *a, **k: True)
    monkeypatch.setattr(agent_mod, "delete_credentials", lambda *a, **k: True)

    gui.do_login()

    assert gui.agent.api_base == "http://current.local:5001"
