"""
AnnounceFlow - Windows Agent
System tray application for quick access and management.
"""
import os
import json
import socket
import sys
import tempfile
import webbrowser
import logging
import time
import uuid
import tkinter as tk
from tkinter import ttk, filedialog
from typing import Optional, Dict, Any
from concurrent.futures import ThreadPoolExecutor
import threading
from logging.handlers import RotatingFileHandler
import requests
from urllib.parse import urlparse
try:
    from PIL import Image, ImageTk
    _HAS_PIL = True
except ImportError:
    _HAS_PIL = False

# Credential management
from credential_manager import (
    save_credentials,
    get_credentials,
    delete_credentials,
    has_credentials,
)
from stream_client import StreamClient

# Configuration
API_BASE = "http://aflow.local:5001"
CONFIG_FILE = "agent_config.json"
DEFAULT_TIMEOUT = (2, 5)
LOGIN_TIMEOUT = (2, 10)
UPLOAD_TIMEOUT = (3, 30)

logger = logging.getLogger(__name__)
stream_logger = logging.getLogger("agent.stream")


# --------------- Logging Setup ---------------

def _resolve_agent_runtime_dir() -> str:
    """Resolve writable runtime directory for logs/reports."""
    if os.name == "nt":
        local_app_data = os.environ.get("LOCALAPPDATA", "").strip()
        if local_app_data:
            return os.path.join(local_app_data, "AnnounceFlow")
    return os.path.join(os.path.expanduser("~"), ".announceflow")


AGENT_RUNTIME_DIR = _resolve_agent_runtime_dir()
AGENT_LOG_DIR = os.path.join(AGENT_RUNTIME_DIR, "logs")
AGENT_LOG_FILE = os.path.join(AGENT_LOG_DIR, "agent.log")
AGENT_STREAM_LOG_FILE = os.path.join(AGENT_LOG_DIR, "agent_stream.log")
AGENT_DEVICE_ID_FILE = os.path.join(AGENT_RUNTIME_DIR, "device_id.txt")


def setup_agent_logging() -> None:
    """Configure rotating logs for app-wide and stream-specific diagnostics."""
    root = logging.getLogger()
    if getattr(root, "_announceflow_agent_logging_ready", False):
        return

    root.setLevel(logging.INFO)
    active_log_dir = AGENT_LOG_DIR
    try:
        os.makedirs(active_log_dir, exist_ok=True)
    except OSError as exc:
        fallback_log_dir = os.path.join(tempfile.gettempdir(), "AnnounceFlow", "logs")
        try:
            os.makedirs(fallback_log_dir, exist_ok=True)
            active_log_dir = fallback_log_dir
            logging.getLogger(__name__).warning(
                "Agent log directory not writable (%s), using fallback %s",
                exc,
                fallback_log_dir,
            )
        except OSError as fallback_exc:
            active_log_dir = ""
            logging.getLogger(__name__).warning(
                "Agent logs disabled; no writable log directory. primary=%s fallback=%s",
                exc,
                fallback_exc,
            )

    if active_log_dir:
        runtime_dir = os.path.dirname(active_log_dir)
        os.environ["ANNOUNCEFLOW_AGENT_RUNTIME_DIR"] = runtime_dir
    else:
        runtime_dir = AGENT_RUNTIME_DIR

    formatter = logging.Formatter(
        "%(asctime)s.%(msecs)03d - %(levelname)s - [%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    agent_log_file = os.path.join(active_log_dir, "agent.log") if active_log_dir else None
    stream_log_file = os.path.join(active_log_dir, "agent_stream.log") if active_log_dir else None

    if agent_log_file:
        try:
            root_file_handler = RotatingFileHandler(
                agent_log_file,
                maxBytes=1_000_000,
                backupCount=5,
                encoding="utf-8",
            )
            root_file_handler.setFormatter(formatter)
            root.addHandler(root_file_handler)
        except OSError as exc:
            logging.getLogger(__name__).warning(
                "Failed to attach agent.log handler (%s)", exc
            )

    stream_handler_attached = False
    if stream_log_file:
        try:
            stream_file_handler = RotatingFileHandler(
                stream_log_file,
                maxBytes=1_000_000,
                backupCount=8,
                encoding="utf-8",
            )
            stream_file_handler.setFormatter(formatter)
            stream_logger.setLevel(logging.INFO)
            stream_logger.addHandler(stream_file_handler)
            stream_handler_attached = True
        except OSError as exc:
            logging.getLogger(__name__).warning(
                "Failed to attach agent_stream.log handler (%s)", exc
            )

    # Avoid duplicate writes to both stream file and root handlers.
    stream_logger.propagate = not stream_handler_attached

    if not getattr(root, "_announceflow_console_handler_attached", False):
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setFormatter(formatter)
        root.addHandler(console_handler)
        root._announceflow_console_handler_attached = True

    root._announceflow_agent_logging_ready = True
    logger.info("Agent runtime dir: %s", runtime_dir)
    logger.info("Agent logging initialized: %s", agent_log_file or "disabled")
    logger.info("Agent stream logging initialized: %s", stream_log_file or "disabled")

# --------------- Theme Colors ---------------

_BG = "#f4f7fb"              # Main background (light)
_BG_HEADER = "#e6edf7"       # Header background
_BG_CARD = "#ffffff"         # Card/section background
_FG = "#1f2a37"              # Primary text
_FG_DIM = "#5f6f82"          # Dimmed text
_GREEN = "#2e7d32"           # Success / play
_GREEN_HOVER = "#388e3c"
_RED = "#c62828"             # Stop / error
_RED_HOVER = "#d32f2f"
_AMBER = "#b7791f"           # Stream / warning
_AMBER_HOVER = "#c58a34"
_BLUE = "#1f6fb2"            # Info / web
_BLUE_HOVER = "#2780ca"
_INDIGO = "#3f51b5"          # Upload
_INDIGO_HOVER = "#5c6bc0"
_STATUS_SUCCESS = "#2e7d32"
_STATUS_ERROR = "#c62828"
_DISABLED_BG = "#ccd6e3"
_SLIDER_TRACK = "#d8e0ec"
_STREAM_STOP = "#95661a"
_BTN_MUTED = "#9eaabb"
_BTN_MUTED_HOVER = "#adb8c6"


def load_agent_config():
    """Load agent configuration."""
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, "r") as f:
            return json.load(f)
    return {"api_base": API_BASE}


def save_agent_config(config):
    """Save agent configuration."""
    with open(CONFIG_FILE, "w") as f:
        json.dump(config, f, indent=2)


def _load_or_create_device_id() -> str:
    """Load stable device_id from disk or create one on first run."""
    try:
        os.makedirs(AGENT_RUNTIME_DIR, exist_ok=True)
    except OSError:
        pass

    try:
        with open(AGENT_DEVICE_ID_FILE, "r", encoding="utf-8") as f:
            existing = f.read().strip()
        if existing:
            return existing
    except OSError:
        pass

    device_id = f"agent-{uuid.uuid4()}"
    try:
        with open(AGENT_DEVICE_ID_FILE, "w", encoding="utf-8") as f:
            f.write(device_id)
    except OSError as exc:
        logger.warning("Could not persist device_id, using volatile id: %s", exc)
    return device_id


def get_icon(name, size=(24, 24)):
    """Load and resize icon from assets."""
    if not _HAS_PIL:
        return None
    base = getattr(sys, '_MEIPASS', os.path.dirname(__file__))
    icon_path = os.path.join(base, 'assets', 'icons', f'{name}.png')
    try:
        if os.path.exists(icon_path):
            img = Image.open(icon_path)
            img = img.resize(size, Image.Resampling.LANCZOS)
            return ImageTk.PhotoImage(img)
    except Exception as e:
        logger.error("Error loading icon %s: %s", name, e)
    return None

class ModernButton(tk.Frame):
    """Custom button for cross-platform consistency with PNG icon support."""

    def __init__(self, parent, text, command, bg_color, hover_color, icon_name=None,
                 font_size=11, pady=10, **kwargs):
        super().__init__(parent, bg=bg_color, cursor="hand2", **kwargs)
        self.command = command
        self.bg_color = bg_color
        self.hover_color = hover_color
        self._disabled = False

        # Keep reference to image to prevent garbage collection
        self.icon_image = None
        if icon_name:
            self.icon_image = get_icon(icon_name)

        # Internal container to center content
        content_frame = tk.Frame(self, bg=bg_color)
        content_frame.pack(expand=True, fill="both", padx=16, pady=pady)

        self.icon_label = None
        if self.icon_image:
            self.icon_label = tk.Label(content_frame, image=self.icon_image, bg=bg_color)
            self.icon_label.pack(side="left", padx=(0, 8))

        self.text_label = tk.Label(
            content_frame, text=text, bg=bg_color, fg="white",
            font=("Segoe UI", font_size, "bold")
        )
        self.text_label.pack(side="left" if self.icon_image else "top")

        # Bind events for all sub-components
        widgets_to_bind = [self, content_frame, self.text_label]
        if self.icon_label:
            widgets_to_bind.append(self.icon_label)

        for widget in widgets_to_bind:
            widget.bind("<Enter>", self.on_enter)
            widget.bind("<Leave>", self.on_leave)
            widget.bind("<Button-1>", self.on_click)

    def set_disabled(self, disabled: bool):
        """Enable or disable the button."""
        self._disabled = disabled
        if disabled:
            self.config(bg=_DISABLED_BG)
            for child in self.winfo_children():
                child.config(bg=_DISABLED_BG)
                for subchild in child.winfo_children():
                    subchild.config(bg=_DISABLED_BG)
            self.config(cursor="")
        else:
            self.config(bg=self.bg_color, cursor="hand2")
            for child in self.winfo_children():
                child.config(bg=self.bg_color)
                for subchild in child.winfo_children():
                    subchild.config(bg=self.bg_color)

    def set_text(self, text: str):
        """Update button text."""
        self.text_label.config(text=text)

    def set_color(self, bg_color: str, hover_color: str):
        """Update button background and hover colors."""
        self.bg_color = bg_color
        self.hover_color = hover_color
        if not self._disabled:
            self.config(bg=bg_color)
            for child in self.winfo_children():
                child.config(bg=bg_color)  # type: ignore[union-attr]
                for subchild in child.winfo_children():
                    subchild.config(bg=bg_color)  # type: ignore[union-attr]

    def on_enter(self, event):
        if self._disabled:
            return
        self.config(bg=self.hover_color)
        for child in self.winfo_children():
            child.config(bg=self.hover_color)
            for subchild in child.winfo_children():
                subchild.config(bg=self.hover_color)

    def on_leave(self, event):
        if self._disabled:
            return
        self.config(bg=self.bg_color)
        for child in self.winfo_children():
            child.config(bg=self.bg_color)
            for subchild in child.winfo_children():
                subchild.config(bg=self.bg_color)

    def on_click(self, event):
        if self._disabled:
            return
        if self.command:
            self.command()


class ModernSlider(tk.Frame):
    """Modern volume slider with Canvas - thick bar with speaker icons."""

    def __init__(self, parent, from_=0, to=100, value=80, command=None, card_bg=None, **kwargs):
        bg = card_bg or _BG
        super().__init__(parent, bg=bg, **kwargs)
        self._bg = bg
        self.from_ = from_
        self.to = to
        self.value = value
        self.command = command

        # Load PNG icons (requires PIL — optional)
        self.img_mute = get_icon('vol_mute', size=(18, 18))
        self.img_loud = get_icon('vol_high', size=(18, 18))

        container = tk.Frame(self, bg=bg)
        container.pack(fill="x", expand=True)

        # Left label: PNG icon when available, plain text fallback (no emoji)
        self.left_icon = tk.Label(container, bg=bg)
        if self.img_mute:
            self.left_icon.config(image=self.img_mute)
        else:
            self.left_icon.config(text="Ses", fg=_FG_DIM, font=("Segoe UI", 9))
        self.left_icon.pack(side="left", padx=(0, 8))

        # Slider canvas — thin track design, fixed 32px height
        self.canvas = tk.Canvas(
            container, height=32, bg=bg,
            highlightthickness=0, cursor="hand2",
        )
        self.canvas.pack(side="left", fill="x", expand=True)

        # Right side: optional loud icon + percentage
        right_frame = tk.Frame(container, bg=bg)
        right_frame.pack(side="left", padx=(8, 0))

        if self.img_loud:
            tk.Label(right_frame, bg=bg, image=self.img_loud).pack(side="left", padx=(0, 4))

        self.percent_label = tk.Label(
            right_frame, text=f"{int(value)}%",
            font=("Segoe UI", 10, "bold"), bg=bg, fg=_GREEN, width=4,
        )
        self.percent_label.pack(side="left")

        self.canvas.bind("<Button-1>", self._on_click)
        self.canvas.bind("<B1-Motion>", self._on_click)
        self.bind("<Map>", lambda e: self.after(50, self._draw))

    # Track geometry constants (shared between _draw and _on_click)
    _PAD = 8
    _HANDLE_R = 8

    def _draw(self):
        self.canvas.delete("all")
        w = max(self.canvas.winfo_width(), 200)
        h = 32
        pad = self._PAD
        handle_r = self._HANDLE_R

        track_y = h // 2
        track_h = 4          # thin, clean track line
        track_top = track_y - track_h // 2
        track_bottom = track_y + track_h // 2
        track_left = pad
        track_right = w - pad
        track_span = max(1, track_right - track_left)

        # Gray base track
        self.canvas.create_rectangle(
            track_left, track_top, track_right, track_bottom,
            fill=_SLIDER_TRACK, outline="",
        )

        # Green fill — simple rectangle, no end-cap tricks, no overflow
        ratio = (self.value - self.from_) / max(1, self.to - self.from_)
        fill_right = track_left + ratio * track_span
        if ratio > 0:
            self.canvas.create_rectangle(
                track_left, track_top, fill_right, track_bottom,
                fill=_GREEN, outline="",
            )

        # Handle — clamped so it never extends beyond the track edges
        handle_x = track_left + ratio * track_span
        handle_x = max(track_left + handle_r, min(track_right - handle_r, handle_x))
        self.canvas.create_oval(
            handle_x - handle_r, track_y - handle_r,
            handle_x + handle_r, track_y + handle_r,
            fill="white", outline=_GREEN, width=2,
        )

        self.percent_label.config(text=f"{int(self.value)}%")

    def _on_click(self, event):
        pad = self._PAD
        w = max(self.canvas.winfo_width(), 200)
        track_span = max(1, w - pad - pad)
        ratio = (event.x - pad) / track_span
        ratio = max(0, min(1, ratio))
        self.value = self.from_ + ratio * (self.to - self.from_)
        self._draw()
        if self.command:
            self.command(self.value)

    def set_value(self, value):
        """Set slider value programmatically."""
        self.value = max(self.from_, min(self.to, value))
        self._draw()


class AnnounceFlowAgent:
    """Main agent application."""

    def __init__(self):
        self.config = load_agent_config()
        self.api_base = self.config.get("api_base", API_BASE)
        self.device_id = _load_or_create_device_id()
        self.session = None
        self._session_lock = threading.RLock()

    def close(self):
        """Release network resources."""
        with self._session_lock:
            if self.session is not None:
                try:
                    self.session.close()
                except Exception:
                    pass
                self.session = None

    def _request(
        self, method: str, path: str, *,
        auth_required: bool = True, timeout=DEFAULT_TIMEOUT, **kwargs,
    ) -> Optional[requests.Response]:
        """Issue HTTP request with explicit timeout and optional auth session."""
        url = f"{self.api_base}{path}"
        try:
            if auth_required:
                with self._session_lock:
                    session = self.session
                if session is None:
                    return None
                return session.request(method=method.upper(), url=url, timeout=timeout, **kwargs)
            return requests.request(method=method.upper(), url=url, timeout=timeout, **kwargs)
        except requests.exceptions.RequestException:
            return None

    def login(self, username: str, password: str) -> Dict[str, Any]:
        """Login to the API with detailed error handling."""
        session = None
        try:
            session = requests.Session()
            session.post(
                f"{self.api_base}/login",
                data={"username": username, "password": password},
                allow_redirects=True,
                timeout=LOGIN_TIMEOUT,
            )
            if "session" in session.cookies:
                with self._session_lock:
                    old_session = self.session
                    self.session = session
                if old_session is not None:
                    try:
                        old_session.close()
                    except Exception:
                        pass
                return {"success": True}
            session.close()
            return {"success": False, "error": "invalid_credentials"}
        except requests.exceptions.ConnectionError:
            if session is not None:
                session.close()
            logger.error("Connection error: Cannot reach %s", self.api_base)
            return {"success": False, "error": "connection_error"}
        except requests.exceptions.Timeout:
            if session is not None:
                session.close()
            logger.error("Connection timeout to %s", self.api_base)
            return {"success": False, "error": "timeout"}
        except Exception as e:
            if session is not None:
                session.close()
            logger.error("Login error: %s", e)
            return {"success": False, "error": "unknown"}

    def discover_server(self, port=5001):
        """Scan local network for AnnounceFlow server on given port."""
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            local_ip = s.getsockname()[0]
            s.close()
        except Exception:
            return None

        parts = local_ip.split(".")
        if len(parts) != 4:
            return None
        subnet = ".".join(parts[:3])

        for i in range(1, 255):
            ip = f"{subnet}.{i}"
            if ip == local_ip:
                continue
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(0.15)
                result = sock.connect_ex((ip, port))
                sock.close()
                if result == 0:
                    try:
                        url = f"http://{ip}:{port}"
                        resp = requests.get(f"{url}/api/health", timeout=DEFAULT_TIMEOUT)
                        if resp.ok and "player" in resp.json():
                            return url
                    except Exception:
                        pass
            except Exception:
                pass
        return None

    def get_media_files(self):
        """Fetch all media files."""
        response = self._request("GET", "/api/now-playing", auth_required=True)
        if response is None:
            return []
        try:
            return response.json() if response.ok else []
        except ValueError:
            return []

    def get_health(self):
        """Fetch system health including current volume (no auth required)."""
        response = self._request("GET", "/api/health", auth_required=False)
        if response is None:
            return {}
        try:
            return response.json() if response.ok else {}
        except ValueError:
            return {}

    def play_file(self, media_id):
        """Play a media file."""
        response = self._request(
            "POST", "/api/play", auth_required=True, json={"media_id": media_id},
        )
        return bool(response and response.ok)

    def stop_playback(self):
        """Stop playback."""
        response = self._request("POST", "/api/stop", auth_required=True)
        return bool(response and response.ok)

    def start_playlist(self):
        """Start background music playlist (loop)."""
        response = self._request("POST", "/api/playlist/start-all", auth_required=True)
        return bool(response and response.ok)

    def stop_playlist(self):
        """Stop background music playlist."""
        response = self._request("POST", "/api/playlist/stop", auth_required=True)
        return bool(response and response.ok)

    def set_volume(self, volume):
        """Set volume level."""
        response = self._request(
            "POST", "/api/volume", auth_required=True, json={"volume": volume},
        )
        return bool(response and response.ok)

    def start_stream_with_details(
        self, correlation_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """Start stream receiver on server and return structured result."""
        headers = {}
        if correlation_id:
            headers["X-Stream-Correlation-Id"] = correlation_id
        device_id = getattr(self, "device_id", None)
        if isinstance(device_id, str) and device_id.strip():
            headers["X-Stream-Device-Id"] = device_id.strip()
        response = self._request(
            "POST",
            "/api/stream/start",
            auth_required=True,
            headers=headers if headers else None,
        )
        if response is None:
            return {"success": False, "error": "api_start_failed"}
        try:
            payload = response.json()
        except (ValueError, AttributeError):
            return {
                "success": False,
                "error": "api_start_invalid_response",
                "http_status": getattr(response, "status_code", None),
            }

        ok = bool(response.ok and payload.get("success"))
        result = {
            "success": ok,
            "http_status": getattr(response, "status_code", None),
            "status": payload.get("status"),
            "correlation_id": correlation_id,
        }
        if not ok:
            result["error"] = payload.get("error", "api_start_failed")
        return result

    def stop_stream_with_details(self) -> Dict[str, Any]:
        """Stop stream receiver on server and return structured result."""
        response = self._request("POST", "/api/stream/stop", auth_required=True)
        if response is None:
            return {"success": False, "error": "api_stop_failed"}
        try:
            payload = response.json()
        except (ValueError, AttributeError):
            return {
                "success": False,
                "error": "api_stop_invalid_response",
                "http_status": getattr(response, "status_code", None),
            }

        ok = bool(response.ok and payload.get("success"))
        result = {
            "success": ok,
            "http_status": getattr(response, "status_code", None),
            "status": payload.get("status"),
        }
        if not ok:
            result["error"] = payload.get("error", "api_stop_failed")
        return result

    def send_heartbeat(self) -> bool:
        """Send stream heartbeat to keep the session alive."""
        device_id = getattr(self, "device_id", None)
        headers = {}
        if isinstance(device_id, str) and device_id.strip():
            headers["X-Stream-Device-Id"] = device_id.strip()
            
        response = self._request(
            "POST",
            "/api/stream/heartbeat",
            auth_required=True,
            headers=headers if headers else None,
            timeout=DEFAULT_TIMEOUT,
        )
        return bool(response and response.ok)

    def get_stream_status(self) -> Dict[str, Any]:
        """Get stream status to detect if someone else took over."""
        response = self._request("GET", "/api/stream/status", auth_required=True)
        if response is None:
            return {}
        try:
            return response.json() if response.ok else {}
        except ValueError:
            return {}

    def start_stream(self) -> bool:
        """Backward-compatible bool API for stream start."""
        return bool(self.start_stream_with_details(correlation_id=None).get("success"))

    def stop_stream(self) -> bool:
        """Backward-compatible bool API for stream stop."""
        return bool(self.stop_stream_with_details().get("success"))

    def upload_file(self, filepath, media_type="announcement"):
        """Upload a media file."""
        try:
            with open(filepath, "rb") as f:
                files = {"file": (os.path.basename(filepath), f)}
                data = {"media_type": media_type}
                response = self._request(
                    "POST", "/api/media/upload", auth_required=True,
                    timeout=UPLOAD_TIMEOUT, files=files, data=data,
                )
            return bool(response and response.ok)
        except Exception as e:
            logger.error("Upload error: %s", e)
            return False


class NetworkWorker:
    """Run network jobs outside Tkinter UI thread and marshal results back safely."""

    def __init__(self, root: tk.Tk, max_workers: int = 4):
        self._root = root
        self._executor = ThreadPoolExecutor(
            max_workers=max(1, int(max_workers)), thread_name_prefix="agent-net"
        )
        self._lock = threading.RLock()
        self._closed = False

    def submit(self, fn, *, on_success=None, on_error=None):
        with self._lock:
            if self._closed:
                return
            future = self._executor.submit(fn)

        def _done(done_future):
            try:
                result = done_future.result()
            except Exception as exc:
                self._dispatch(on_error, exc)
                return
            self._dispatch(on_success, result)

        future.add_done_callback(_done)

    def _dispatch(self, callback, payload):
        if callback is None:
            return
        try:
            self._root.after(0, lambda: callback(payload))
        except tk.TclError:
            return

    def shutdown(self):
        with self._lock:
            if self._closed:
                return
            self._closed = True
        self._executor.shutdown(wait=False, cancel_futures=True)


class AgentGUI:
    """GUI for the agent."""

    _STATUS_DISPLAY_MS = 4000  # How long status messages stay visible

    def __init__(self, agent):
        self.agent = agent
        self.root: Optional[tk.Tk] = None
        self.logged_in = False
        self.network_worker: Optional[NetworkWorker] = None
        self._closing = False
        self._volume_update_job = None
        self._pending_volume: Optional[int] = None
        self._stream_client = StreamClient()
        self._status_clear_job = None
        # Button references for loading state
        self._btn_music_start: Optional[ModernButton] = None
        self._btn_music_stop: Optional[ModernButton] = None
        self._btn_stream_start: Optional[ModernButton] = None
        self._btn_stream_stop: Optional[ModernButton] = None
        self._btn_upload: Optional[ModernButton] = None
        
        # State tracking for polling loops
        self._stream_active = False
        self._stream_poll_active = False
        self._music_active = False
        self._heartbeat_job = None
        self._poll_job = None

    def run(self):
        """Run the GUI application."""
        self.root = tk.Tk()
        self.root.title("AnnounceFlow")
        self.root.geometry("420x760")
        self.root.minsize(400, 700)
        self.root.configure(bg=_BG)
        self.network_worker = NetworkWorker(self.root)
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

        # Style
        style = ttk.Style()
        style.theme_use("clam")
        style.configure("TButton", padding=10, font=("Segoe UI", 10))
        style.configure("TLabel", background=_BG, foreground=_FG, font=("Segoe UI", 10))
        style.configure("TEntry", padding=5)

        # Try auto-login if credentials are saved
        self._try_auto_login()

        try:
            self.root.mainloop()
        finally:
            self.on_close()

    def _root_alive(self) -> bool:
        return bool(self.root and self.root.winfo_exists() and not self._closing)

    def _submit_network_job(self, fn, *, on_success=None, on_error=None):
        worker = self.network_worker
        if not worker or not self._root_alive():
            return
        if on_error is None:
            on_error = self._handle_network_error
        worker.submit(fn, on_success=on_success, on_error=on_error)

    def _handle_network_error(self, exc: Exception):
        """Default UI bridge for unexpected worker exceptions."""
        logger.exception("Network worker job failed: %s", exc)
        if not self._root_alive():
            return
        self._show_status("Ağ hatası oluştu. Tekrar deneyin.", error=True)

    # --------------- Status Bar ---------------

    def _show_status(self, message: str, error: bool = False):
        """Show a status message in the status bar (no pop-up)."""
        if not self._root_alive():
            return
        if not hasattr(self, "_status_label"):
            return
        try:
            if not self._status_label.winfo_exists():
                return
        except tk.TclError:
            return

        color = _STATUS_ERROR if error else _STATUS_SUCCESS
        self._status_label.config(text=message, fg=color)

        # Clear previous timer
        if self._status_clear_job is not None:
            try:
                self.root.after_cancel(self._status_clear_job)
            except tk.TclError:
                pass

        # Auto-clear after delay
        delay = 6000 if error else self._STATUS_DISPLAY_MS
        self._status_clear_job = self.root.after(delay, self._clear_status)

    def _clear_status(self):
        """Clear the status bar text."""
        self._status_clear_job = None
        if not self._root_alive():
            return
        if hasattr(self, "_status_label"):
            try:
                if self._status_label.winfo_exists():
                    self._status_label.config(text="")
            except tk.TclError:
                pass

    # --------------- Loading State Helpers ---------------

    def _with_loading(self, btn: Optional[ModernButton], original_text: str,
                      loading_text: str = "İşleniyor..."):
        """Context: disable button, run job, re-enable."""
        if btn:
            btn.set_disabled(True)
            btn.set_text(loading_text)

        def restore():
            if btn and self._root_alive():
                try:
                    if btn.winfo_exists():
                        btn.set_disabled(False)
                        btn.set_text(original_text)
                except tk.TclError:
                    pass

        return restore

    # --------------- Window Lifecycle ---------------

    def on_close(self):
        """Bounded-time, idempotent UI shutdown."""
        if self._closing:
            return
        self._closing = True

        if self.root and self._volume_update_job is not None:
            try:
                self.root.after_cancel(self._volume_update_job)
            except tk.TclError:
                pass
            self._volume_update_job = None

        if hasattr(self, '_stream_client'):
            self._stream_client.stop_sender()

        if self.network_worker:
            self.network_worker.shutdown()
            self.network_worker = None

        self.agent.close()

        if self.root and self.root.winfo_exists():
            try:
                self.root.destroy()
            except tk.TclError:
                pass

    # --------------- Auto Login ---------------

    def _try_auto_login(self):
        """Attempt auto-login with saved credentials."""
        if not has_credentials(self.agent.api_base):
            self.show_login_frame()
            return

        creds = get_credentials(self.agent.api_base)
        if not creds:
            self.show_login_frame()
            return

        # Show loading state
        self.clear_frame()
        loading_frame = tk.Frame(self.root, bg=_BG)
        loading_frame.pack(expand=True)

        tk.Label(
            loading_frame, text="StatekSound",
            font=("Segoe UI", 20, "bold"), bg=_BG, fg=_FG,
        ).pack(pady=(40, 10))
        tk.Label(
            loading_frame, text="Otomatik giriş yapılıyor...",
            bg=_BG, fg=_AMBER, font=("Segoe UI", 10),
        ).pack(pady=20)

        username, password = creds

        def _job():
            return self.agent.login(username, password)

        def _on_done(result):
            if not self._root_alive():
                return
            if result.get("success"):
                self.logged_in = True
                self._stream_poll_active = True
                self._run_status_poll()
                self.show_main_frame()
                return
            if result.get("error") == "invalid_credentials":
                delete_credentials(self.agent.api_base)
                self.show_login_frame(error_message="Şifreniz değişti, lütfen tekrar girin.")
                return
            self.show_login_frame(error_message="Sunucuya bağlanılamadı.")

        self._submit_network_job(_job, on_success=_on_done)

    # --------------- Login Frame ---------------

    def show_login_frame(self, error_message: str = None):
        """Show login screen."""
        if self.root and self._volume_update_job is not None:
            try:
                self.root.after_cancel(self._volume_update_job)
            except tk.TclError:
                pass
            self._volume_update_job = None
        self._pending_volume = None

        self.clear_frame()

        frame = tk.Frame(self.root, bg=_BG)
        frame.pack(expand=True)

        # Title
        tk.Label(
            frame, text="StatekSound",
            font=("Segoe UI", 20, "bold"), bg=_BG, fg=_FG,
        ).pack(pady=(30, 5))

        tk.Label(
            frame, text="Ses Yönetim Sistemi",
            font=("Segoe UI", 10), bg=_BG, fg=_FG_DIM,
        ).pack(pady=(0, 25))

        # Server URL
        url_frame = tk.Frame(frame, bg=_BG)
        url_frame.pack(pady=0, fill="x", padx=40)

        tk.Label(url_frame, text="Sunucu Adresi:", bg=_BG, fg=_FG_DIM).pack(anchor="w")
        self.url_entry = tk.Entry(url_frame, font=("Segoe UI", 10), width=35)
        self.url_entry.insert(0, self.agent.api_base)
        self.url_entry.pack(fill="x", pady=5)

        # Username
        tk.Label(url_frame, text="Kullanıcı Adı:", bg=_BG, fg=_FG_DIM).pack(
            anchor="w", pady=(10, 0)
        )
        self.username_entry = tk.Entry(url_frame, font=("Segoe UI", 10), width=35)
        self.username_entry.insert(0, "admin")
        self.username_entry.pack(fill="x", pady=5)

        # Password
        tk.Label(url_frame, text="Şifre:", bg=_BG, fg=_FG_DIM).pack(
            anchor="w", pady=(10, 0)
        )
        self.password_entry = tk.Entry(
            url_frame, font=("Segoe UI", 10), width=35, show="*"
        )
        self.password_entry.pack(fill="x", pady=5)
        self.username_entry.bind("<Return>", lambda *_: self.do_login())
        self.password_entry.bind("<Return>", lambda *_: self.do_login())

        # Remember Me checkbox
        self.remember_var = tk.BooleanVar(value=True)
        remember_frame = tk.Frame(url_frame, bg=_BG)
        remember_frame.pack(fill="x", pady=(10, 0))

        remember_cb = tk.Checkbutton(
            remember_frame, text="Beni Hatırla", variable=self.remember_var,
            bg=_BG, fg=_FG_DIM, selectcolor=_BG_CARD,
            activebackground=_BG, activeforeground=_FG_DIM,
            font=("Segoe UI", 9),
        )
        remember_cb.pack(anchor="w")

        # Login button
        login_btn = ModernButton(
            frame, text="Giriş Yap", command=self.do_login,
            bg_color=_INDIGO, hover_color=_INDIGO_HOVER,
        )
        login_btn.pack(pady=20, fill="x", padx=40)

        self.status_label = tk.Label(frame, text="", bg=_BG, fg=_RED)
        self.status_label.pack()

        if error_message:
            self.status_label.config(text=error_message, fg=_AMBER)

    def do_login(self):
        """Handle login."""
        url = self.url_entry.get().strip().rstrip("/")
        username = self.username_entry.get().strip()
        password = self.password_entry.get()
        remember = self.remember_var.get()

        self.agent.api_base = url
        self.agent.config["api_base"] = url
        save_agent_config(self.agent.config)

        self.status_label.config(text="Bağlanılıyor...", fg=_AMBER)

        def _job():
            first = self.agent.login(username, password)
            if first.get("success"):
                return {"result": first, "resolved_url": self.agent.api_base}
            if first.get("error") != "connection_error":
                return {"result": first, "resolved_url": self.agent.api_base}

            found_url = self.agent.discover_server()
            if not found_url:
                return {"result": first, "resolved_url": None}

            self.agent.api_base = found_url
            retry = self.agent.login(username, password)
            return {"result": retry, "resolved_url": found_url}

        def _on_done(payload):
            if not self._root_alive():
                return

            result = payload.get("result", {})
            resolved_url = payload.get("resolved_url")

            if result.get("success"):
                final_url = resolved_url or url
                self.agent.config["api_base"] = final_url
                save_agent_config(self.agent.config)
                if hasattr(self, "url_entry"):
                    try:
                        if self.url_entry.winfo_exists():
                            self.url_entry.delete(0, tk.END)
                            self.url_entry.insert(0, final_url)
                    except tk.TclError:
                        pass

                if remember:
                    save_credentials(final_url, username, password)
                else:
                    delete_credentials(final_url)

                self.logged_in = True
                self._stream_poll_active = True
                self._run_status_poll()
                self.show_main_frame()
                return

            error = result.get("error", "unknown")
            if error == "invalid_credentials":
                self.status_label.config(
                    text="Giriş başarısız! Bilgileri kontrol edin.", fg=_RED
                )
                return
            if error == "connection_error":
                self.status_label.config(text="Sunucu ağda bulunamadı!", fg=_RED)
                return
            if error == "timeout":
                self.status_label.config(text="Bağlantı zaman aşımına uğradı!", fg=_RED)
                return
            self.status_label.config(text="Beklenmeyen bir hata oluştu.", fg=_RED)

        self._submit_network_job(_job, on_success=_on_done)

    # --------------- Main Frame ---------------

    def show_main_frame(self):
        """Show main control panel."""
        self.clear_frame()

        # Header with logout link
        header = tk.Frame(self.root, bg=_BG_HEADER, pady=12)
        header.pack(fill="x")

        header_content = tk.Frame(header, bg=_BG_HEADER)
        header_content.pack(fill="x", padx=16)

        tk.Label(
            header_content, text="AnnounceFlow",
            font=("Segoe UI", 14, "bold"), bg=_BG_HEADER, fg=_FG,
        ).pack(side="left")

        # Logout as small text link in header
        logout_label = tk.Label(
            header_content, text="Çıkış", font=("Segoe UI", 10, "underline"),
            bg=_BG_HEADER, fg=_FG, cursor="hand2",
        )
        logout_label.pack(side="right")
        logout_label.bind("<Button-1>", lambda e: self.logout())
        logout_label.bind("<Enter>", lambda e: logout_label.config(fg=_RED))
        logout_label.bind("<Leave>", lambda e: logout_label.config(fg=_FG))


        # Scrollable content
        content = tk.Frame(self.root, bg=_BG, padx=16, pady=12)
        content.pack(fill="both", expand=True)

        # ── Music Section ──
        music_section = tk.Frame(content, bg=_BG_CARD, padx=12, pady=10)
        music_section.pack(fill="x", pady=(0, 8))

        tk.Label(
            music_section, text="Müzik Kontrolü",
            font=("Segoe UI", 11, "bold"), bg=_BG_CARD, fg=_FG,
        ).pack(anchor="w", pady=(0, 8))

        music_btns = tk.Frame(music_section, bg=_BG_CARD)
        music_btns.pack(fill="x")

        self._btn_music_start = ModernButton(
            music_btns, text="Başlat", command=self.start_music,
            bg_color=_GREEN, hover_color=_GREEN_HOVER, icon_name="play",
            font_size=10, pady=8,
        )
        self._btn_music_start.pack(side="left", fill="x", expand=True, padx=(0, 4))

        self._btn_music_stop = ModernButton(
            music_btns, text="Durdur", command=self.stop_music,
            bg_color=_RED, hover_color=_RED_HOVER, icon_name="stop",
            font_size=10, pady=8,
        )
        self._btn_music_stop.pack(side="left", fill="x", expand=True, padx=(4, 0))

        # ── Stream Section ──
        stream_section = tk.Frame(content, bg=_BG_CARD, padx=12, pady=10)
        stream_section.pack(fill="x", pady=(0, 8))

        tk.Label(
            stream_section, text="Canlı Yayın",
            font=("Segoe UI", 11, "bold"), bg=_BG_CARD, fg=_FG,
        ).pack(anchor="w", pady=(0, 8))

        stream_btns = tk.Frame(stream_section, bg=_BG_CARD)
        stream_btns.pack(fill="x")

        self._btn_stream_start = ModernButton(
            stream_btns, text="Yayını Başlat", command=self.start_stream,
            bg_color=_AMBER, hover_color=_AMBER_HOVER, icon_name="stream",
            font_size=10, pady=8,
        )
        self._btn_stream_start.pack(side="left", fill="x", expand=True, padx=(0, 4))

        self._btn_stream_stop = ModernButton(
            stream_btns, text="Yayını Durdur", command=self.stop_stream,
            bg_color=_STREAM_STOP, hover_color=_AMBER, icon_name="stop",
            font_size=10, pady=8,
        )
        self._btn_stream_stop.pack(side="left", fill="x", expand=True, padx=(4, 0))

        self._refresh_music_buttons()
        self._refresh_stream_buttons()

        # ── Tools Section ──
        tools_section = tk.Frame(content, bg=_BG_CARD, padx=12, pady=10)
        tools_section.pack(fill="x", pady=(0, 8))

        tk.Label(
            tools_section, text="Araçlar",
            font=("Segoe UI", 11, "bold"), bg=_BG_CARD, fg=_FG,
        ).pack(anchor="w", pady=(0, 8))

        tools_btns = tk.Frame(tools_section, bg=_BG_CARD)
        tools_btns.pack(fill="x")

        self._btn_upload = ModernButton(
            tools_btns, text="Anons Yükle", command=self.upload_announcement,
            bg_color=_INDIGO, hover_color=_INDIGO_HOVER, icon_name="upload",
            font_size=10, pady=8,
        )
        self._btn_upload.pack(side="left", fill="x", expand=True, padx=(0, 4))

        ModernButton(
            tools_btns, text="Web Panel", command=self.open_web_panel,
            bg_color=_BLUE, hover_color=_BLUE_HOVER, icon_name="web",
            font_size=10, pady=8,
        ).pack(side="left", fill="x", expand=True, padx=(4, 0))

        # ── Volume Section ──
        vol_section = tk.Frame(content, bg=_BG_CARD, padx=12, pady=10)
        vol_section.pack(fill="x", pady=(0, 8))

        tk.Label(
            vol_section, text="Ses Seviyesi",
            font=("Segoe UI", 11, "bold"), bg=_BG_CARD, fg=_FG,
        ).pack(anchor="w", pady=(0, 4))

        self.volume_slider = ModernSlider(
            vol_section, from_=0, to=100, value=80, command=self.on_volume_change,
            card_bg=_BG_CARD,
        )
        self.volume_slider.pack(fill="x")

        # Sync volume from server
        def _load_health():
            return self.agent.get_health()

        def _apply_health(health):
            if not self._root_alive():
                return
            if not hasattr(self, "volume_slider"):
                return
            player = health.get("player", {}) if isinstance(health, dict) else {}
            current = player.get("volume")
            if isinstance(current, (int, float)):
                self.volume_slider.set_value(int(current))
            player_state = player.get("state", "")
            if isinstance(player_state, str) and player_state.lower() in ("playing", "running"):
                self._music_active = True
                self._refresh_music_buttons()

        self._submit_network_job(_load_health, on_success=_apply_health)

        # ── Status Bar ──
        status_frame = tk.Frame(self.root, bg=_BG_HEADER, pady=6)
        status_frame.pack(fill="x", side="bottom")

        self._status_label = tk.Label(
            status_frame, text="", font=("Segoe UI", 9),
            bg=_BG_HEADER, fg=_FG_DIM,
        )
        self._status_label.pack()

        tk.Label(
            status_frame,
            text="Statek Stabil Teknoloji tarafından geliştirilmiştir.",
            font=("Segoe UI", 8), bg=_BG_HEADER, fg=_FG_DIM,
        ).pack(pady=(0, 2))

    # --------------- Actions ---------------

    def start_music(self):
        """Start background music playlist (loop)."""
        restore = self._with_loading(self._btn_music_start, "Başlat", "Başlatılıyor...")

        def _on_done(success):
            restore()
            if not self._root_alive():
                return
            if success:
                self._music_active = True
                self._refresh_music_buttons()
                self._show_status("Müzik başlatıldı")
            else:
                self._show_status("Müzik başlatılamadı", error=True)

        self._submit_network_job(
            lambda: self.agent.start_playlist(), on_success=_on_done,
        )

    def stop_music(self):
        """Stop current playback."""
        restore = self._with_loading(self._btn_music_stop, "Durdur", "Durduruluyor...")

        def _on_done(success):
            restore()
            if not self._root_alive():
                return
            if success:
                self._music_active = False
                self._refresh_music_buttons()
                self._show_status("Müzik durduruldu")
            else:
                self._show_status("Müzik durdurulamadı", error=True)

        self._submit_network_job(
            lambda: self.agent.stop_playlist(), on_success=_on_done,
        )

    def upload_announcement(self):
        """Upload an announcement file."""
        filepath = filedialog.askopenfilename(
            title="Anons Dosyası Seç",
            filetypes=[("Audio Files", "*.mp3 *.wav *.ogg"), ("All Files", "*.*")],
        )

        if filepath:
            restore = self._with_loading(self._btn_upload, "Anons Yükle", "Yükleniyor...")

            def _on_done(success):
                restore()
                if not self._root_alive():
                    return
                if success:
                    self._show_status("Anons yüklendi")
                else:
                    self._show_status("Dosya yüklenemedi", error=True)

            self._submit_network_job(
                lambda: self.agent.upload_file(filepath, "announcement"),
                on_success=_on_done,
            )

    def open_web_panel(self):
        """Open web panel in browser."""
        webbrowser.open(self.agent.api_base)

    def _resolve_stream_host(self) -> str:
        """Extract Pi4 hostname from api_base URL."""
        parsed = urlparse(self.agent.api_base)
        return parsed.hostname or "aflow.local"

    def start_stream(self):
        """Start live stream to Pi4."""
        host = self._resolve_stream_host()
        correlation_id = f"agent-{int(time.time() * 1000)}"
        restore = self._with_loading(
            self._btn_stream_start, "Yayını Başlat", "Bağlanıyor..."
        )

        def _job():
            api_result = self.agent.start_stream_with_details(
                correlation_id=correlation_id
            )
            if not api_result.get("success"):
                return {"result": "api_fail", "api": api_result}

            sender_ok = self._stream_client.start_sender(
                host, 5800, correlation_id=correlation_id
            )
            if not sender_ok:
                rollback_result = self.agent.stop_stream_with_details()
                return {
                    "result": "sender_fail",
                    "rollback": rollback_result,
                    "attempt": self._stream_client.get_attempt_snapshot(),
                    "report": self._stream_client.build_failure_report(),
                }

            # Catch short-lived starts that die immediately after startup check.
            time.sleep(0.8)
            if not self._stream_client.is_alive():
                self._stream_client.record_external_failure(
                    "capture_thread_died",
                    "Capture thread ended shortly after startup",
                )
                rollback_result = self.agent.stop_stream_with_details()
                return {
                    "result": "sender_fail",
                    "rollback": rollback_result,
                    "attempt": self._stream_client.get_attempt_snapshot(),
                    "report": self._stream_client.build_failure_report(),
                }
            return {"result": "ok", "attempt": self._stream_client.get_attempt_snapshot()}

        def _on_done(payload):
            restore()
            if not self._root_alive():
                return

            if not isinstance(payload, dict):
                payload = {"result": payload}

            result = payload.get("result")
            if result == "ok":
                attempt = payload.get("attempt") or {}
                attempt_id = attempt.get("attempt_id")
                stream_logger.info(
                    "stream_start_ok attempt_id=%s correlation_id=%s packet_count=%s",
                    attempt_id,
                    correlation_id,
                    attempt.get("packet_count"),
                )
                self._stream_active = True
                self._start_stream_polling_loops()
                self._refresh_stream_buttons()
                self._show_status("Canlı yayın başlatıldı")
            elif result == "sender_fail":
                attempt = payload.get("attempt") or {}
                attempt_id = attempt.get("attempt_id")
                error_code = attempt.get("error_code") or self._stream_client.last_error
                error_messages = {
                    "resolve_failed": "Sunucu adresi çözülemedi",
                    "no_audio_device": "Ses cihazı bulunamadı",
                    "recorder_open_failed": "Ses yakalama başlatılamadı",
                    "udp_send_failed": "Ağ üzerinden ses gönderilemedi",
                    "capture_thread_died": "Ses yakalama başlatıldı ama anında durdu",
                    "capture_error": "Ses yakalama hatası oluştu",
                }
                msg = error_messages.get(
                    error_code, "Yayın başlatılamadı"
                )
                stream_logger.error(
                    "stream_start_sender_fail attempt_id=%s correlation_id=%s error_code=%s rollback=%s",
                    attempt_id,
                    correlation_id,
                    error_code,
                    (payload.get("rollback") or {}).get("success"),
                )
                logger.error("%s", payload.get("report"))
                self._show_status(f"{msg} (detay log'a yazıldı)", error=True)
            else:
                api = payload.get("api") or {}
                api_error = api.get("error", "api_start_failed")
                api_messages = {
                    "api_start_failed": "Sunucuya bağlanılamadı",
                    "api_start_invalid_response": "Sunucudan geçersiz cevap alındı",
                    "receiver_start_failed": "Sunucu yayın alıcısını başlatamadı",
                    "stream_already_live": "Yayın zaten aktif. Önce mevcut yayını durdurun.",
                }
                stream_logger.error(
                    "stream_start_api_fail correlation_id=%s error_code=%s http_status=%s",
                    correlation_id,
                    api_error,
                    api.get("http_status"),
                )
                self._show_status(
                    f"{api_messages.get(api_error, 'Yayın başlatılamadı')} (detay log'a yazıldı)",
                    error=True,
                )

        self._submit_network_job(_job, on_success=_on_done)

    def stop_stream(self):
        """Stop live stream."""
        restore = self._with_loading(
            self._btn_stream_stop, "Yayını Durdur", "Durduruluyor..."
        )

        def _job():
            self._stream_client.stop_sender()
            return self.agent.stop_stream_with_details()

        def _on_done(payload):
            restore()
            if not self._root_alive():
                return
            if not isinstance(payload, dict):
                payload = {"success": bool(payload)}
            success = bool(payload.get("success"))
            
            self._stream_active = False
            self._stop_heartbeat_only()
            self._refresh_stream_buttons()

            if success:
                self._show_status("Yayın durduruldu")
            else:
                stream_logger.error(
                    "stream_stop_failed error_code=%s http_status=%s",
                    payload.get("error"),
                    payload.get("http_status"),
                )
                self._show_status("Yayın durdurulamadı", error=True)

        self._submit_network_job(_job, on_success=_on_done)

    def on_volume_change(self, value):
        """Handle volume change."""
        self._pending_volume = int(float(value))
        if not self.root:
            return
        if self._volume_update_job is not None:
            self.root.after_cancel(self._volume_update_job)
        self._volume_update_job = self.root.after(120, self._flush_pending_volume)

    def _flush_pending_volume(self):
        self._volume_update_job = None
        if self._pending_volume is None:
            return
        volume = self._pending_volume
        self._pending_volume = None
        self._submit_network_job(lambda: self.agent.set_volume(volume))

    def logout(self):
        """Logout and return to login screen."""
        self._stream_active = False
        self._music_active = False
        self._stop_stream_polling_loops()
        delete_credentials(self.agent.api_base)
        self.agent.close()
        # Ensure any active stream is stopped
        if hasattr(self, '_stream_client'):
            self._stream_client.stop_sender()
        self.logged_in = False
        self.show_login_frame()

    def _refresh_music_buttons(self):
        """Update music button colors to reflect current play state."""
        if not self._btn_music_start or not self._btn_music_stop:
            return
        if self._music_active:
            self._btn_music_start.set_color(_BTN_MUTED, _BTN_MUTED_HOVER)
            self._btn_music_stop.set_color(_RED, _RED_HOVER)
        else:
            self._btn_music_start.set_color(_GREEN, _GREEN_HOVER)
            self._btn_music_stop.set_color(_BTN_MUTED, _BTN_MUTED_HOVER)

    def _refresh_stream_buttons(self):
        """Update stream button colors to reflect current stream state."""
        if not self._btn_stream_start or not self._btn_stream_stop:
            return
        if self._stream_active:
            self._btn_stream_start.set_color(_BTN_MUTED, _BTN_MUTED_HOVER)
            self._btn_stream_stop.set_color(_STREAM_STOP, _AMBER)
        else:
            self._btn_stream_start.set_color(_AMBER, _AMBER_HOVER)
            self._btn_stream_stop.set_color(_BTN_MUTED, _BTN_MUTED_HOVER)

    def _start_stream_polling_loops(self):
        """Start the loops that maintain stream health and watch for takeovers."""
        if not self._root_alive():
            return
        self._stop_heartbeat_only()
        if not self._stream_poll_active:
            self._stream_poll_active = True
            self._run_status_poll()
        self._heartbeat_job = self.root.after(4500, self._run_heartbeat)

    def _stop_stream_polling_loops(self):
        self._stream_poll_active = False
        self._stop_heartbeat_only()
        if self._poll_job is not None and self.root:
            try:
                self.root.after_cancel(self._poll_job)
            except tk.TclError:
                pass
            self._poll_job = None

    def _stop_heartbeat_only(self):
        """Stop heartbeat loop but keep status poll running."""
        if self._heartbeat_job is not None and self.root:
            try:
                self.root.after_cancel(self._heartbeat_job)
            except tk.TclError:
                pass
            self._heartbeat_job = None

    def _run_heartbeat(self):
        """Send a heartbeat. Loop if still active."""
        if not self._stream_active or not self._root_alive():
            return
            
        def _job():
            return self.agent.send_heartbeat()
            
        def _on_done(success):
            if not self._root_alive():
                return
            if not success:
                logger.warning("Agent stream heartbeat failed")
            # Schedule next regardless of success, server drops us if too many fail
            if self._stream_active:
                self._heartbeat_job = self.root.after(4500, self._run_heartbeat)
                
        self._submit_network_job(_job, on_success=_on_done)

    def _run_status_poll(self):
        """Check stream state and react: takeover, external stop, or auto-resume.

        Keeps polling as long as _stream_poll_active is True, even when
        _stream_active (sender running) is False.  This allows the agent
        to detect when the panel re-starts the receiver and auto-resume
        the local sender.
        """
        if not getattr(self, "_stream_poll_active", False) or not self._root_alive():
            return

        def _job():
            return self.agent.get_stream_status()

        def _on_done(status):
            if not self._root_alive():
                return

            is_active = status.get("active")
            state = status.get("state", "idle")
            owner_device_id = status.get("owner_device_id")
            my_device_id = getattr(self.agent, "device_id", None)

            # ── 1. Takeover: someone else owns the stream ──────────────
            if is_active and owner_device_id and my_device_id and owner_device_id != my_device_id:
                stream_logger.info("stream_takeover_detected new_owner=%s", owner_device_id)
                self._stream_active = False
                self._stop_heartbeat_only()
                self._refresh_stream_buttons()
                self._submit_network_job(
                    lambda: self._stream_client.stop_sender(),
                    on_success=lambda _: self._show_status("Yayın başka bir cihaza devredildi!", error=True)
                )
                # Keep polling
                if getattr(self, "_stream_poll_active", False):
                    self._poll_job = self.root.after(3000, self._run_status_poll)
                return

            # ── 2. External stop: server idle/error, we were sending ───
            if state in ("idle", "error") and not is_active and self._stream_active:
                stream_logger.info(
                    "stream_external_stop detected state=%s, stopping local sender", state
                )
                self._stream_active = False
                self._stop_heartbeat_only()
                self._refresh_stream_buttons()
                self._submit_network_job(
                    lambda: self._stream_client.stop_sender(),
                    on_success=lambda _: self._show_status(
                        "Yayın durduruldu" if state == "idle" else "Yayın bağlantısı kesildi",
                        error=(state == "error"),
                    ),
                )
                # Keep polling — don't return, schedule next poll below

            # ── 3. Auto-resume: receiver is live but sender stopped ────
            elif is_active and state == "live" and not self._stream_active:
                stream_logger.info(
                    "stream_auto_resume: receiver is live (owner=%s) "
                    "but local sender stopped — restarting sender",
                    owner_device_id or "panel",
                )
                host = self._resolve_stream_host()
                correlation_id = f"agent-resume-{int(time.time() * 1000)}"

                def _resume():
                    ok = self._stream_client.start_sender(
                        host, 5800, correlation_id=correlation_id
                    )
                    stream_logger.info(
                        "stream_auto_resume: start_sender result=%s "
                        "host=%s correlation_id=%s",
                        ok, host, correlation_id,
                    )
                    return ok

                def _on_resume(ok):
                    if not self._root_alive():
                        return
                    if ok:
                        stream_logger.info(
                            "stream_auto_resume: sender resumed successfully"
                        )
                        self._stream_active = True
                        self._start_stream_polling_loops()
                        self._refresh_stream_buttons()
                        self._show_status("Yayın devam ediyor")
                    else:
                        stream_logger.warning(
                            "stream_auto_resume: sender failed to resume, "
                            "will retry on next poll"
                        )
                        # Don't set _stream_active — next poll will retry

                self._submit_network_job(_resume, on_success=_on_resume)
                return  # _start_stream_polling_loops will reschedule

            # ── 4. Steady state: keep polling ──────────────────────────
            if getattr(self, "_stream_poll_active", False):
                interval = 2000 if self._stream_active else 3000
                self._poll_job = self.root.after(interval, self._run_status_poll)

        self._submit_network_job(_job, on_success=_on_done)

    def clear_frame(self):
        """Clear all widgets from root."""
        if self.root:
            for widget in self.root.winfo_children():
                widget.destroy()


def main():
    """Main entry point."""
    setup_agent_logging()
    logger.info("AnnounceFlow Agent starting")
    agent = AnnounceFlowAgent()
    gui = AgentGUI(agent)
    gui.run()


if __name__ == "__main__":
    main()
