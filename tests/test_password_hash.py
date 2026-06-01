"""BL-04 — Password hashing tests."""
import json
import os
import sys
from unittest.mock import patch

import pytest
from werkzeug.security import check_password_hash, generate_password_hash

# Ensure project root is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from web_panel import app, _verify_password


# --------------- unit: _verify_password ---------------


class TestVerifyPassword:
    def test_plaintext_match(self):
        """Legacy plaintext password matches."""
        assert _verify_password("admin123", "admin123") is True

    def test_plaintext_mismatch(self):
        assert _verify_password("wrong", "admin123") is False

    def test_hash_match(self):
        hashed = generate_password_hash("mypassword")
        assert _verify_password("mypassword", hashed) is True

    def test_hash_mismatch(self):
        hashed = generate_password_hash("mypassword")
        assert _verify_password("wrong", hashed) is False


# --------------- integration: login ---------------


@pytest.fixture
def client(tmp_path):
    """Flask test client with temporary config."""
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps({
        "admin_username": "admin",
        "admin_password": "admin123",
    }))
    app.config["TESTING"] = True
    app.config["SECRET_KEY"] = "test-secret"

    # Reset ConfigService singleton so it reloads from patched path
    from services.config_service import ConfigService
    ConfigService._instance = None

    with patch.object(ConfigService, "_ensure_loaded") as mock_load:
        def _fake_load(self_inner):
            self_inner._config_path = str(config_path)
            self_inner._config = {"admin_username": "admin", "admin_password": "admin123"}
            if os.path.exists(str(config_path)):
                with open(str(config_path), "r") as f:
                    self_inner._config.update(json.load(f))
            self_inner._initialized = True

        mock_load.side_effect = lambda: _fake_load(ConfigService._instance)

        # Re-init singleton with patched loader
        ConfigService._instance = None
        svc = ConfigService()
        svc._config_path = str(config_path)
        svc._config = json.loads(config_path.read_text())
        svc._initialized = True

        with app.test_client() as c:
            yield c, config_path

    # Cleanup singleton
    ConfigService._instance = None


class TestLoginFlow:
    def test_correct_login_redirects_to_index(self, client):
        """Login with correct credentials should redirect to index."""
        c, _ = client
        resp = c.post("/login", data={
            "username": "admin",
            "password": "admin123",
        }, follow_redirects=False)
        assert resp.status_code == 302
        assert "/" in resp.headers["Location"]

    def test_wrong_password_stays_on_login(self, client):
        c, _ = client
        resp = c.post("/login", data={
            "username": "admin",
            "password": "wrongpass",
        }, follow_redirects=False)
        assert resp.status_code == 200

    def test_change_password_saves_hash(self, client):
        """Password change should store as hash."""
        c, config_path = client
        # Login first
        c.post("/login", data={
            "username": "admin",
            "password": "admin123",
        })
        # Change password
        resp = c.post("/change-password", data={
            "password": "newstrongpass",
            "password_confirm": "newstrongpass",
        }, follow_redirects=False)
        assert resp.status_code == 302

        # Verify config has hashed password
        config = json.loads(config_path.read_text())
        stored = config["admin_password"]
        assert stored.startswith("scrypt:") or stored.startswith("pbkdf2:")

    def test_hashed_login(self, client):
        """Login with hashed password should work."""
        c, config_path = client
        config = json.loads(config_path.read_text())
        config["admin_password"] = generate_password_hash("strongpass")
        config_path.write_text(json.dumps(config))

        from services.config_service import ConfigService
        svc = ConfigService()
        svc._config = json.loads(config_path.read_text())

        resp = c.post("/login", data={
            "username": "admin",
            "password": "strongpass",
        }, follow_redirects=False)
        assert resp.status_code == 302

    def test_recovery_login_resets_admin_password_and_forces_change(self, client):
        """Emergency admin/admin123 login resets forgotten credentials."""
        c, config_path = client
        config = json.loads(config_path.read_text())
        config["admin_username"] = "field-admin"
        config["admin_password"] = generate_password_hash("forgotten-pass")
        config_path.write_text(json.dumps(config))

        from services.config_service import ConfigService
        svc = ConfigService()
        svc._config = json.loads(config_path.read_text())

        resp = c.post("/login", data={
            "username": "admin",
            "password": "admin123",
        }, follow_redirects=False)

        assert resp.status_code == 302
        assert "/change-password" in resp.headers["Location"]

        saved = json.loads(config_path.read_text())
        assert saved["admin_username"] == "admin"
        assert saved["admin_password"] != "admin123"
        assert check_password_hash(saved["admin_password"], "admin123")

        with c.session_transaction() as sess:
            assert sess["logged_in"] is True
            assert sess["force_password_change"] is True

    def test_force_password_change_blocks_protected_pages_until_changed(self, client):
        c, _ = client
        with c.session_transaction() as sess:
            sess["logged_in"] = True
            sess["force_password_change"] = True

        resp = c.get("/", follow_redirects=False)

        assert resp.status_code == 302
        assert "/change-password" in resp.headers["Location"]

    def test_change_password_clears_recovery_force_flag(self, client):
        c, config_path = client
        with c.session_transaction() as sess:
            sess["logged_in"] = True
            sess["force_password_change"] = True

        resp = c.post("/change-password", data={
            "password": "newstrongpass",
            "password_confirm": "newstrongpass",
        }, follow_redirects=False)

        assert resp.status_code == 302
        with c.session_transaction() as sess:
            assert "force_password_change" not in sess

        saved = json.loads(config_path.read_text())
        assert check_password_hash(saved["admin_password"], "newstrongpass")
