"""
AnnounceFlow - Windows Agent
System tray application for quick access and management.
"""
import os
import json
import socket
import sys
import webbrowser
import logging
import tkinter as tk
from tkinter import ttk, filedialog
from typing import Optional, Dict, Any
from concurrent.futures import ThreadPoolExecutor
import threading
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

    def __init__(self, parent, from_=0, to=100, value=80, command=None, **kwargs):
        super().__init__(parent, bg=_BG)
        self.from_ = from_
        self.to = to
        self.value = value
        self.command = command

        # Load SVG/PNG Icons for slider
        self.img_mute = get_icon('vol_mute', size=(20, 20))
        self.img_loud = get_icon('vol_high', size=(20, 20))

        # Main container with icons
        container = tk.Frame(self, bg=_BG)
        container.pack(fill="x", expand=True)

        # Left speaker icon (mute)
        self.left_icon = tk.Label(container, bg=_BG)
        if self.img_mute:
            self.left_icon.config(image=self.img_mute)
        else:
            self.left_icon.config(text="🔈", fg=_FG_DIM, font=("Segoe UI", 14))

        self.left_icon.pack(side="left", padx=(0, 10))

        # Canvas for slider
        self.canvas = tk.Canvas(
            container, width=220, height=50, bg=_BG,
            highlightthickness=0, cursor="hand2",
        )
        self.canvas.pack(side="left", fill="x", expand=True)

        # Right speaker icon (loud) + percentage
        right_frame = tk.Frame(container, bg=_BG)
        right_frame.pack(side="left", padx=(10, 0))

        self.right_icon = tk.Label(right_frame, bg=_BG)
        if self.img_loud:
            self.right_icon.config(image=self.img_loud)
        else:
            self.right_icon.config(text="🔊", fg=_FG_DIM, font=("Segoe UI", 14))

        self.right_icon.pack(side="left")

        self.percent_label = tk.Label(
            right_frame, text=f"{int(value)}%",
            font=("Segoe UI", 12, "bold"), bg=_BG, fg=_GREEN, width=4,
        )
        self.percent_label.pack(side="left", padx=(5, 0))

        # Bind events
        self.canvas.bind("<Button-1>", self._on_click)
        self.canvas.bind("<B1-Motion>", self._on_click)
        self.bind("<Map>", lambda e: self.after(50, self._draw))

    def _draw(self):
        self.canvas.delete("all")
        w = max(self.canvas.winfo_width(), 200)
        h = 50

        pad = 10
        bar_height = 24
        track_y = h // 2
        track_top = track_y - bar_height // 2
        track_bottom = track_y + bar_height // 2
        track_right = w - pad

        radius = bar_height // 2
        self.canvas.create_rectangle(
            pad + radius, track_top, track_right - radius, track_bottom,
            fill=_SLIDER_TRACK, outline="",
        )
        self.canvas.create_oval(
            pad, track_top, pad + bar_height, track_bottom, fill=_SLIDER_TRACK, outline=""
        )
        self.canvas.create_oval(
            track_right - bar_height, track_top, track_right, track_bottom,
            fill=_SLIDER_TRACK, outline="",
        )

        ratio = (self.value - self.from_) / max(1, self.to - self.from_)
        fill_width = ratio * (track_right - pad - bar_height)
        fill_x = pad + bar_height // 2 + fill_width

        if ratio > 0.02:
            self.canvas.create_rectangle(
                pad + radius, track_top, min(fill_x, track_right - radius), track_bottom,
                fill=_GREEN, outline="",
            )
            self.canvas.create_oval(
                pad, track_top, pad + bar_height, track_bottom,
                fill=_GREEN, outline="",
            )

        handle_x = pad + bar_height // 2 + fill_width
        handle_radius = 14
        self.canvas.create_oval(
            handle_x - handle_radius, track_y - handle_radius,
            handle_x + handle_radius, track_y + handle_radius,
            fill="white", outline=_GREEN, width=3,
        )

        self.percent_label.config(text=f"{int(self.value)}%")

    def _on_click(self, event):
        w = max(self.canvas.winfo_width(), 200)
        pad = 10
        bar_height = 24
        track_width = w - pad * 2 - bar_height

        ratio = (event.x - pad - bar_height // 2) / max(1, track_width)
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

    def start_stream(self) -> bool:
        """Start stream: tell Pi4 to start receiver."""
        response = self._request("POST", "/api/stream/start", auth_required=True)
        try:
            return bool(response and response.ok and response.json().get("success"))
        except (ValueError, AttributeError):
            return False

    def stop_stream(self) -> bool:
        """Stop stream: tell Pi4 to stop receiver."""
        response = self._request("POST", "/api/stream/stop", auth_required=True)
        try:
            return bool(response and response.ok and response.json().get("success"))
        except (ValueError, AttributeError):
            return False

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
            loading_frame, text="AnnounceFlow",
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
            frame, text="AnnounceFlow",
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
            header_content, text="Çıkış", font=("Segoe UI", 9, "underline"),
            bg=_BG_HEADER, fg=_FG_DIM, cursor="hand2",
        )
        logout_label.pack(side="right")
        logout_label.bind("<Button-1>", lambda e: self.logout())
        logout_label.bind("<Enter>", lambda e: logout_label.config(fg=_RED))
        logout_label.bind("<Leave>", lambda e: logout_label.config(fg=_FG_DIM))

        # Connection info
        tk.Label(
            header, text=self.agent.api_base,
            font=("Segoe UI", 8), bg=_BG_HEADER, fg=_FG_DIM,
        ).pack(anchor="w", padx=16)

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
            vol_section, from_=0, to=100, value=80, command=self.on_volume_change
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

        self._submit_network_job(_load_health, on_success=_apply_health)

        # ── Status Bar ──
        status_frame = tk.Frame(self.root, bg=_BG_HEADER, pady=6)
        status_frame.pack(fill="x", side="bottom")

        self._status_label = tk.Label(
            status_frame, text="", font=("Segoe UI", 9),
            bg=_BG_HEADER, fg=_FG_DIM,
        )
        self._status_label.pack()

    # --------------- Actions ---------------

    def start_music(self):
        """Start background music playlist (loop)."""
        restore = self._with_loading(self._btn_music_start, "Başlat", "Başlatılıyor...")

        def _on_done(success):
            restore()
            if not self._root_alive():
                return
            if success:
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
        restore = self._with_loading(
            self._btn_stream_start, "Yayını Başlat", "Bağlanıyor..."
        )

        def _job():
            api_ok = self.agent.start_stream()
            if not api_ok:
                return "api_fail"
            sender_ok = self._stream_client.start_sender(host, 5800)
            if not sender_ok:
                self.agent.stop_stream()  # ROLLBACK
                return "sender_fail"
            return "ok"

        def _on_done(result):
            restore()
            if not self._root_alive():
                return
            if result == "ok":
                self._show_status("Canlı yayın başlatıldı")
            elif result == "sender_fail":
                error_messages = {
                    "no_audio_device": "Ses cihazı bulunamadı",
                    "capture_error": "Ses yakalama hatası oluştu",
                }
                msg = error_messages.get(
                    self._stream_client.last_error, "Yayın başlatılamadı"
                )
                self._show_status(msg, error=True)
            else:
                self._show_status("Sunucuya bağlanılamadı", error=True)

        self._submit_network_job(_job, on_success=_on_done)

    def stop_stream(self):
        """Stop live stream."""
        restore = self._with_loading(
            self._btn_stream_stop, "Yayını Durdur", "Durduruluyor..."
        )

        def _job():
            self._stream_client.stop_sender()
            return self.agent.stop_stream()

        def _on_done(success):
            restore()
            if not self._root_alive():
                return
            if success:
                self._show_status("Yayın durduruldu")
            else:
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
        delete_credentials(self.agent.api_base)
        self.agent.close()
        self.logged_in = False
        self.show_login_frame()

    def clear_frame(self):
        """Clear all widgets from root."""
        if self.root:
            for widget in self.root.winfo_children():
                widget.destroy()


def main():
    """Main entry point."""
    agent = AnnounceFlowAgent()
    gui = AgentGUI(agent)
    gui.run()


if __name__ == "__main__":
    main()
