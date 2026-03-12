"""
Release metadata helpers.
"""
from __future__ import annotations

import json


def load_release_stamp(path: str = "release_stamp.json") -> dict:
    """Load release metadata generated during deployment."""
    fallback = {
        "commit": "unknown",
        "commit_short": "unknown",
        "ref": "unknown",
        "branch": "unknown",
        "deployed_at_utc": "unknown",
    }
    try:
        with open(path, "r", encoding="utf-8") as f:
            loaded = json.load(f)
        if not isinstance(loaded, dict):
            return dict(fallback)
    except (OSError, json.JSONDecodeError):
        return dict(fallback)

    release = dict(fallback)
    for key in fallback:
        value = loaded.get(key)
        if isinstance(value, str) and value.strip():
            release[key] = value.strip()
    return release
