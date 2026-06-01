#!/usr/bin/env python3
"""Reset AnnounceFlow admin credentials from the server shell."""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from getpass import getpass
from pathlib import Path
from typing import Any

from werkzeug.security import generate_password_hash


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config.json"
DEFAULT_ENV_PATH = PROJECT_ROOT / ".env"
ADMIN_OVERRIDE_ENV_KEYS = (
    "ANNOUNCEFLOW_ADMIN_USERNAME",
    "ADMIN_USERNAME",
    "ANNOUNCEFLOW_ADMIN_PASSWORD",
    "ADMIN_PASSWORD",
)


def _load_config(config_path: Path) -> dict[str, Any]:
    if not config_path.exists():
        return {}

    with config_path.open("r", encoding="utf-8") as f:
        data = json.load(f)

    if not isinstance(data, dict):
        raise ValueError(f"{config_path} must contain a JSON object")

    return data


def _atomic_write_json(config_path: Path, data: dict[str, Any]) -> None:
    config_path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(
        prefix=f".{config_path.name}.", suffix=".tmp", dir=str(config_path.parent)
    )
    temp_path = Path(temp_name)

    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4, ensure_ascii=False)
            f.write("\n")
        os.replace(temp_path, config_path)
    except Exception:
        try:
            temp_path.unlink(missing_ok=True)
        finally:
            raise


def _env_file_admin_overrides(env_path: Path) -> tuple[str, ...]:
    if not env_path.exists():
        return ()

    keys: list[str] = []
    with env_path.open("r", encoding="utf-8") as f:
        for raw_line in f:
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue

            key = line.split("=", 1)[0].strip()
            if key in ADMIN_OVERRIDE_ENV_KEYS:
                keys.append(key)

    return tuple(keys)


def _process_admin_overrides() -> tuple[str, ...]:
    return tuple(
        key
        for key in ADMIN_OVERRIDE_ENV_KEYS
        if isinstance(os.environ.get(key), str) and os.environ[key].strip()
    )


def reset_admin_password(config_path: Path, username: str, password: str) -> str:
    username = username.strip()
    if not username:
        raise ValueError("username cannot be empty")
    if len(password) < 6:
        raise ValueError("password must be at least 6 characters")

    config = _load_config(config_path)
    password_hash = generate_password_hash(password)
    config["admin_username"] = username
    config["admin_password"] = password_hash
    _atomic_write_json(config_path, config)
    return password_hash


def _prompt_password() -> str:
    while True:
        password = getpass("New admin password: ")
        confirm = getpass("Repeat new admin password: ")

        if password != confirm:
            print("Passwords do not match.", file=sys.stderr)
            continue

        if len(password) < 6:
            print("Password must be at least 6 characters.", file=sys.stderr)
            continue

        return password


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Reset the AnnounceFlow web panel admin username/password."
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=DEFAULT_CONFIG_PATH,
        help=f"config.json path (default: {DEFAULT_CONFIG_PATH})",
    )
    parser.add_argument(
        "--env-file",
        type=Path,
        default=DEFAULT_ENV_PATH,
        help=f".env path to inspect for admin overrides (default: {DEFAULT_ENV_PATH})",
    )
    parser.add_argument("--username", default="admin", help="new admin username")
    parser.add_argument(
        "--password",
        default=None,
        help="new admin password; omit to enter it without echo",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    password = args.password if args.password is not None else _prompt_password()

    try:
        reset_admin_password(args.config, args.username, password)
    except Exception as exc:
        print(f"Admin credential reset failed: {exc}", file=sys.stderr)
        return 1

    print(f"Admin credentials reset in {args.config}")
    print(f"Username: {args.username.strip()}")
    print("Password: set to the supplied value")
    print("Restart required: sudo systemctl restart announceflow")

    override_keys = sorted(
        set(_env_file_admin_overrides(args.env_file)) | set(_process_admin_overrides())
    )
    if override_keys:
        print(
            "WARNING: admin credential environment override detected: "
            + ", ".join(override_keys),
            file=sys.stderr,
        )
        print(
            "Update/remove those overrides too, otherwise they can replace config.json after restart.",
            file=sys.stderr,
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
