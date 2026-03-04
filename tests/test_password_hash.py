"""BL-04 — Password hashing and forced change tests."""
import json
import os
import sys
from unittest.mock import patch

import pytest
from werkzeug.security import generate_password_hash

# Ensure project root is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from web_panel import app, _verify_password, _is_default_password


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


# --------------- unit: _is_default_password ---------------


class TestIsDefaultPassword:
    def test_plaintext_default(self):
        assert _is_default_password("admin123") is True

    def test_hashed_default(self):
        hashed = generate_password_hash("admin123")
        assert _is_default_password(hashed) is True

    def test_plaintext_custom(self):
        assert _is_default_password("strongpass") is False

    def test_hashed_custom(self):
        hashed = generate_password_hash("strongpass")
        assert _is_default_password(hashed) is False


# --------------- integration: login + forced change ---------------


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
    def test_default_password_redirects_to_change(self, client):
        """Login with admin123 should redirect to change-password."""
        c, _ = client
        resp = c.post("/login", data={
            "username": "admin",
            "password": "admin123",
        }, follow_redirects=False)
        assert resp.status_code == 302
        assert "/change-password" in resp.headers["Location"]

    def test_wrong_password_stays_on_login(self, client):
        c, _ = client
        resp = c.post("/login", data={
            "username": "admin",
            "password": "wrongpass",
        }, follow_redirects=False)
        assert resp.status_code == 200

    def test_change_password_saves_hash(self, client):
        """After forced change, password should be stored as hash."""
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

    def test_change_password_mismatch(self, client):
        """Mismatched passwords should not save."""
        c, config_path = client
        c.post("/login", data={
            "username": "admin",
            "password": "admin123",
        })
        resp = c.post("/change-password", data={
            "password": "newpass1",
            "password_confirm": "newpass2",
        }, follow_redirects=True)
        assert resp.status_code == 200

        config = json.loads(config_path.read_text())
        assert config["admin_password"] == "admin123"

    def test_change_password_too_short(self, client):
        """Short passwords should be rejected."""
        c, config_path = client
        c.post("/login", data={
            "username": "admin",
            "password": "admin123",
        })
        resp = c.post("/change-password", data={
            "password": "abc",
            "password_confirm": "abc",
        }, follow_redirects=True)
        assert resp.status_code == 200

        config = json.loads(config_path.read_text())
        assert config["admin_password"] == "admin123"

    def test_hashed_login_no_redirect(self, client):
        """Login with non-default hashed password should go to index."""
        c, config_path = client
        # Pre-set a hashed non-default password
        config = json.loads(config_path.read_text())
        config["admin_password"] = generate_password_hash("strongpass")
        config_path.write_text(json.dumps(config))

        # Reload config singleton
        from services.config_service import ConfigService
        svc = ConfigService()
        svc._config = json.loads(config_path.read_text())

        resp = c.post("/login", data={
            "username": "admin",
            "password": "strongpass",
        }, follow_redirects=False)
        assert resp.status_code == 302
        assert "/change-password" not in resp.headers["Location"]
