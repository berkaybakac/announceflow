from pathlib import Path

from web_panel import app


def _login(client):
    with client.session_transaction() as sess:
        sess["logged_in"] = True


def test_agent_download_requires_login():
    app.config["TESTING"] = True
    client = app.test_client()

    resp = client.get("/downloads/agent/latest", follow_redirects=False)

    assert resp.status_code in (301, 302)
    assert "/login" in resp.headers.get("Location", "")


def test_agent_download_returns_404_redirect_when_missing(tmp_path, monkeypatch):
    app.config["TESTING"] = True
    client = app.test_client()
    _login(client)
    missing_path = tmp_path / "missing.exe"
    monkeypatch.setattr("routes.settings_routes.AGENT_EXE_PATH", missing_path)

    resp = client.get("/downloads/agent/latest", follow_redirects=True)

    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "Agent paketi bulunamadı" in html
    assert "Ayarlar" in html


def test_agent_download_returns_file_when_available(tmp_path, monkeypatch):
    app.config["TESTING"] = True
    client = app.test_client()
    _login(client)
    exe_path = Path(tmp_path) / "StatekSound.exe"
    exe_bytes = b"MZdummy-binary"
    exe_path.write_bytes(exe_bytes)
    monkeypatch.setattr("routes.settings_routes.AGENT_EXE_PATH", exe_path)

    resp = client.get("/downloads/agent/latest")

    assert resp.status_code == 200
    assert "attachment; filename=StatekSound.exe" in resp.headers.get(
        "Content-Disposition", ""
    )
    assert resp.data == exe_bytes
