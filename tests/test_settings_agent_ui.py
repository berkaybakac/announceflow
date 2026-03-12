from types import SimpleNamespace

from web_panel import app


def _login(client):
    with client.session_transaction() as sess:
        sess["logged_in"] = True


def test_settings_requires_auth():
    app.config["TESTING"] = True
    client = app.test_client()

    resp = client.get("/settings", follow_redirects=False)

    assert resp.status_code in (301, 302)
    assert "/login" in resp.headers.get("Location", "")


def test_settings_shows_agent_card_missing_state(monkeypatch):
    app.config["TESTING"] = True
    client = app.test_client()
    _login(client)
    import web_panel as wp

    real_isfile = wp.os.path.isfile

    def _isfile_override(path):
        if str(path).endswith("/agent/releases/StatekSound.exe"):
            return False
        return real_isfile(path)

    monkeypatch.setattr("web_panel.os.path.isfile", _isfile_override)

    resp = client.get("/settings")

    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "Windows Agent" in html
    assert "Yok" in html
    assert "Agent İndir" in html
    assert "Son Güncelleme" not in html
    assert "Teknik ekip Windows Agent uygulamasını buradan indirip kurabilir." not in html


def test_settings_shows_agent_card_available_state(monkeypatch):
    app.config["TESTING"] = True
    client = app.test_client()
    _login(client)
    import web_panel as wp

    real_isfile = wp.os.path.isfile
    real_stat = wp.os.stat

    def _isfile_override(path):
        if str(path).endswith("/agent/releases/StatekSound.exe"):
            return True
        return real_isfile(path)

    def _stat_override(path):
        if str(path).endswith("/agent/releases/StatekSound.exe"):
            return SimpleNamespace(st_size=7 * 1024 * 1024, st_mtime=1700000000)
        return real_stat(path)

    monkeypatch.setattr("web_panel.os.path.isfile", _isfile_override)
    monkeypatch.setattr(
        "web_panel.os.stat",
        _stat_override,
    )

    resp = client.get("/settings")

    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "Windows Agent" in html
    assert "Hazır" in html
    assert "7.00 MB" in html
    assert "Son Güncelleme" not in html
