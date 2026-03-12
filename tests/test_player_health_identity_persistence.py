from routes import player_routes


def test_resolve_instance_identity_generates_and_saves(monkeypatch):
    cfg = {"site_name": "Store A"}
    saved = {}

    monkeypatch.setattr(player_routes, "load_config", lambda: cfg)

    def _save_config(payload):
        saved.update(payload)
        return True

    monkeypatch.setattr(player_routes, "save_config", _save_config)

    instance_id, site_name = player_routes._resolve_instance_identity()

    assert instance_id.startswith("af-")
    assert site_name == "Store A"
    assert saved.get("instance_id") == instance_id


def test_resolve_instance_identity_keeps_existing(monkeypatch):
    cfg = {"instance_id": "af-fixed123", "site_name": "Store B"}
    calls = {"count": 0}

    monkeypatch.setattr(player_routes, "load_config", lambda: cfg)

    def _save_config(_payload):
        calls["count"] += 1
        return True

    monkeypatch.setattr(player_routes, "save_config", _save_config)

    instance_id, site_name = player_routes._resolve_instance_identity()

    assert instance_id == "af-fixed123"
    assert site_name == "Store B"
    assert calls["count"] == 0


def test_resolve_instance_identity_uses_hostname_when_site_missing(monkeypatch):
    cfg = {"instance_id": "af-fixed456"}
    monkeypatch.setattr(player_routes, "load_config", lambda: cfg)
    monkeypatch.setattr(player_routes.socket, "gethostname", lambda: "pi-store-host")
    monkeypatch.setattr(player_routes, "save_config", lambda _payload: True)

    instance_id, site_name = player_routes._resolve_instance_identity()

    assert instance_id == "af-fixed456"
    assert site_name == "pi-store-host"
