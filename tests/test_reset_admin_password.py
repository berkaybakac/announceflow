import json

from werkzeug.security import check_password_hash

from scripts.reset_admin_password import (
    _env_file_admin_overrides,
    reset_admin_password,
)


def test_reset_admin_password_updates_existing_config(tmp_path):
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "volume": 80,
                "admin_username": "old-admin",
                "admin_password": "old-password",
            }
        ),
        encoding="utf-8",
    )

    reset_admin_password(config_path, "admin", "admin123")

    config = json.loads(config_path.read_text(encoding="utf-8"))
    assert config["volume"] == 80
    assert config["admin_username"] == "admin"
    assert config["admin_password"] != "admin123"
    assert check_password_hash(config["admin_password"], "admin123")


def test_reset_admin_password_creates_missing_config(tmp_path):
    config_path = tmp_path / "config.json"

    reset_admin_password(config_path, "admin", "newpass123")

    config = json.loads(config_path.read_text(encoding="utf-8"))
    assert config["admin_username"] == "admin"
    assert check_password_hash(config["admin_password"], "newpass123")


def test_env_file_admin_overrides_ignores_comments(tmp_path):
    env_path = tmp_path / ".env"
    env_path.write_text(
        "\n".join(
            [
                "# ANNOUNCEFLOW_ADMIN_PASSWORD=ignored",
                "FLASK_SECRET_KEY=secret",
                "ANNOUNCEFLOW_ADMIN_USERNAME=admin",
                "ADMIN_PASSWORD=override",
            ]
        ),
        encoding="utf-8",
    )

    assert _env_file_admin_overrides(env_path) == (
        "ANNOUNCEFLOW_ADMIN_USERNAME",
        "ADMIN_PASSWORD",
    )
