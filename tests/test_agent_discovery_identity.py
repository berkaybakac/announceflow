import os
import sys

import pytest


_AGENT_DIR = os.path.join(os.path.dirname(__file__), "..", "agent")


@pytest.fixture
def agent_mod():
    sys.path.insert(0, _AGENT_DIR)
    import agent as _agent_mod

    yield _agent_mod

    for key in list(sys.modules.keys()):
        if key == "agent" or key.startswith("agent."):
            del sys.modules[key]
    while _AGENT_DIR in sys.path:
        sys.path.remove(_AGENT_DIR)


class _FakeResp:
    def __init__(self, payload):
        self.ok = True
        self._payload = payload

    def json(self):
        return self._payload


def test_discover_server_filters_by_expected_instance(agent_mod, monkeypatch):
    open_hosts = {"192.168.1.42", "192.168.1.43"}

    class _FakeSocket:
        def __init__(self, *_args, **_kwargs):
            self._kind = _args[1] if len(_args) > 1 else None

        def connect(self, _addr):
            return None

        def getsockname(self):
            return ("192.168.1.10", 0)

        def close(self):
            return None

        def settimeout(self, _timeout):
            return None

        def connect_ex(self, addr):
            return 0 if addr[0] in open_hosts else 1

    def _fake_get(url, timeout):
        if "192.168.1.42" in url:
            return _FakeResp(
                {
                    "status": "ok",
                    "player": {},
                    "identity": {"instance_id": "inst-a", "site_name": "Site A"},
                }
            )
        if "192.168.1.43" in url:
            return _FakeResp(
                {
                    "status": "ok",
                    "player": {},
                    "identity": {"instance_id": "inst-b", "site_name": "Site B"},
                }
            )
        return _FakeResp({"status": "ok", "player": {}})

    monkeypatch.setattr(agent_mod.socket, "socket", _FakeSocket)
    monkeypatch.setattr(agent_mod.requests, "get", _fake_get)

    agent = agent_mod.AnnounceFlowAgent()
    found = agent.discover_server(expected_instance_id="inst-b")

    assert isinstance(found, dict)
    assert found["url"] == "http://192.168.1.43:5001"
    assert found["instance_id"] == "inst-b"


def test_discover_server_returns_none_when_identity_mismatch(agent_mod, monkeypatch):
    open_hosts = {"192.168.1.42"}

    class _FakeSocket:
        def __init__(self, *_args, **_kwargs):
            pass

        def connect(self, _addr):
            return None

        def getsockname(self):
            return ("192.168.1.10", 0)

        def close(self):
            return None

        def settimeout(self, _timeout):
            return None

        def connect_ex(self, addr):
            return 0 if addr[0] in open_hosts else 1

    def _fake_get(_url, timeout):
        return _FakeResp(
            {
                "status": "ok",
                "player": {},
                "identity": {"instance_id": "inst-a", "site_name": "Site A"},
            }
        )

    monkeypatch.setattr(agent_mod.socket, "socket", _FakeSocket)
    monkeypatch.setattr(agent_mod.requests, "get", _fake_get)

    agent = agent_mod.AnnounceFlowAgent()
    found = agent.discover_server(expected_instance_id="inst-z")

    assert found is None


def test_host_ip_cache_prefers_host_profile(agent_mod, monkeypatch):
    monkeypatch.setattr(agent_mod, "save_agent_config", lambda cfg: True)
    agent = agent_mod.AnnounceFlowAgent()
    agent.config = {}

    agent.remember_successful_connection(
        configured_url="http://rpi001.local:5001",
        resolved_url="http://192.168.1.50:5001",
        identity={"instance_id": "inst-1", "site_name": "Site 1"},
    )

    cached = agent.get_cached_ip_url("http://rpi001.local:5001")
    assert cached == "http://192.168.1.50:5001"
    assert agent.config.get("expected_instance_id") == "inst-1"
    assert agent.config.get("expected_site_name") == "Site 1"
