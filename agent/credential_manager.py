"""
AnnounceFlow - Credential Manager
Secure credential storage using OS-native keychains.

Uses keyring library for cross-platform support:
- Windows: Windows Credential Manager
- macOS: Keychain
- Linux: Secret Service (GNOME Keyring, KWallet)
"""
import json
import os
from typing import Optional, Tuple

try:
    import keyring
    from keyring.errors import PasswordDeleteError

    _KEYRING_AVAILABLE = True
except Exception:
    keyring = None
    PasswordDeleteError = Exception
    _KEYRING_AVAILABLE = False

SERVICE_NAME = "AnnounceFlowAgent"
_FALLBACK_PATH = os.path.join(
    os.environ.get("APPDATA", os.path.expanduser("~")),
    "AnnounceFlowAgent",
    "credentials.json",
)


def _get_account_name(server_url: str) -> str:
    """Generate account name from server URL."""
    # Remove protocol and trailing slashes for consistency
    account = server_url.replace("http://", "").replace("https://", "").rstrip("/")
    return account


def _load_fallback_credentials() -> dict:
    """Load fallback credentials from local JSON file."""
    try:
        with open(_FALLBACK_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _save_fallback_credentials(data: dict) -> bool:
    """Persist fallback credentials to local JSON file."""
    try:
        os.makedirs(os.path.dirname(_FALLBACK_PATH), exist_ok=True)
        with open(_FALLBACK_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        try:
            os.chmod(_FALLBACK_PATH, 0o600)
        except OSError:
            pass  # Windows may not support chmod
        return True
    except OSError as e:
        print(f"Error saving fallback credentials: {e}")
        return False


def save_credentials(server_url: str, username: str, password: str) -> bool:
    """
    Save credentials securely using OS keychain.

    Args:
        server_url: API server URL (used as identifier)
        username: Login username
        password: Login password

    Returns:
        True if saved successfully, False otherwise
    """
    account = _get_account_name(server_url)

    if _KEYRING_AVAILABLE:
        try:
            # Store as JSON to keep both username and password
            credential_data = json.dumps({"username": username, "password": password})
            keyring.set_password(SERVICE_NAME, account, credential_data)
            return True
        except Exception as e:
            print(f"Error saving credentials to keyring, falling back to file: {e}")

    # Fallback storage (prevents app crash if keyring backend is unavailable)
    data = _load_fallback_credentials()
    data[account] = {"username": username, "password": password}
    return _save_fallback_credentials(data)


def get_credentials(server_url: str) -> Optional[Tuple[str, str]]:
    """
    Retrieve stored credentials for a server.

    Args:
        server_url: API server URL

    Returns:
        Tuple of (username, password) if found, None otherwise
    """
    account = _get_account_name(server_url)

    if _KEYRING_AVAILABLE:
        try:
            credential_data = keyring.get_password(SERVICE_NAME, account)
            if credential_data:
                data = json.loads(credential_data)
                return (data.get("username"), data.get("password"))
        except Exception as e:
            print(f"Error getting credentials from keyring, falling back to file: {e}")

    data = _load_fallback_credentials().get(account)
    if isinstance(data, dict):
        return (data.get("username"), data.get("password"))
    return None


def delete_credentials(server_url: str) -> bool:
    """
    Delete stored credentials for a server.

    Args:
        server_url: API server URL

    Returns:
        True if deleted successfully, False otherwise
    """
    account = _get_account_name(server_url)

    keyring_ok = True
    if _KEYRING_AVAILABLE:
        try:
            keyring.delete_password(SERVICE_NAME, account)
        except PasswordDeleteError:
            # Password doesn't exist - that's fine
            pass
        except Exception as e:
            print(f"Error deleting keyring credentials: {e}")
            keyring_ok = False

    data = _load_fallback_credentials()
    if account in data:
        del data[account]
        file_ok = _save_fallback_credentials(data)
    else:
        file_ok = True

    return keyring_ok and file_ok


def has_credentials(server_url: str) -> bool:
    """
    Check if credentials exist for a server.

    Args:
        server_url: API server URL

    Returns:
        True if credentials exist, False otherwise
    """
    return get_credentials(server_url) is not None
