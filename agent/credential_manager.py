"""
AnnounceFlow - Credential Manager
Secure credential storage using OS-native keychains.

Uses keyring library for cross-platform support:
- Windows: Windows Credential Manager
- macOS: Keychain
- Linux: Secret Service (GNOME Keyring, KWallet)
"""
import keyring
import json
from typing import Optional, Tuple

SERVICE_NAME = "AnnounceFlowAgent"


def _get_account_name(server_url: str) -> str:
    """Generate account name from server URL."""
    # Remove protocol and trailing slashes for consistency
    account = server_url.replace("http://", "").replace("https://", "").rstrip("/")
    return account


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
    try:
        account = _get_account_name(server_url)
        # Store as JSON to keep both username and password
        credential_data = json.dumps({
            "username": username,
            "password": password
        })
        keyring.set_password(SERVICE_NAME, account, credential_data)
        return True
    except Exception as e:
        print(f"Error saving credentials: {e}")
        return False


def get_credentials(server_url: str) -> Optional[Tuple[str, str]]:
    """
    Retrieve stored credentials for a server.
    
    Args:
        server_url: API server URL
        
    Returns:
        Tuple of (username, password) if found, None otherwise
    """
    try:
        account = _get_account_name(server_url)
        credential_data = keyring.get_password(SERVICE_NAME, account)
        
        if credential_data:
            data = json.loads(credential_data)
            return (data.get("username"), data.get("password"))
        return None
    except Exception as e:
        print(f"Error getting credentials: {e}")
        return None


def delete_credentials(server_url: str) -> bool:
    """
    Delete stored credentials for a server.
    
    Args:
        server_url: API server URL
        
    Returns:
        True if deleted successfully, False otherwise
    """
    try:
        account = _get_account_name(server_url)
        keyring.delete_password(SERVICE_NAME, account)
        return True
    except keyring.errors.PasswordDeleteError:
        # Password doesn't exist - that's fine
        return True
    except Exception as e:
        print(f"Error deleting credentials: {e}")
        return False


def has_credentials(server_url: str) -> bool:
    """
    Check if credentials exist for a server.
    
    Args:
        server_url: API server URL
        
    Returns:
        True if credentials exist, False otherwise
    """
    return get_credentials(server_url) is not None
