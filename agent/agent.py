"""
AnnounceFlow - Windows Agent
System tray application for quick access and management.
"""
import os
import json
import socket
import webbrowser
import logging
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from typing import Optional, Dict, Any
from concurrent.futures import ThreadPoolExecutor
import threading
import requests

# Credential management
from credential_manager import (
    save_credentials,
    get_credentials,
    delete_credentials,
    has_credentials,
)

# Configuration
API_BASE = "http://aflow.local:5001"
CONFIG_FILE = "agent_config.json"
DEFAULT_TIMEOUT = (2, 5)
LOGIN_TIMEOUT = (2, 10)
UPLOAD_TIMEOUT = (3, 30)

logger = logging.getLogger(__name__)


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


class ModernButton(tk.Frame):
    """Custom button for cross-platform consistency (especially Mac)."""

    def __init__(self, parent, text, command, bg_color, hover_color, **kwargs):
        super().__init__(parent, bg=bg_color, cursor="hand2", **kwargs)
        self.command = command
        self.bg_color = bg_color
        self.hover_color = hover_color

        self.label = tk.Label(
            self, text=text, bg=bg_color, fg="white", font=("Segoe UI", 11, "bold")
        )
        self.label.pack(expand=True, fill="both", padx=20, pady=15)

        # Bind events
        for widget in (self, self.label):
            widget.bind("<Enter>", self.on_enter)
            widget.bind("<Leave>", self.on_leave)
            widget.bind("<Button-1>", self.on_click)

    def on_enter(self, event):
        self.config(bg=self.hover_color)
        self.label.config(bg=self.hover_color)

    def on_leave(self, event):
        self.config(bg=self.bg_color)
        self.label.config(bg=self.bg_color)

    def on_click(self, event):
        if self.command:
            self.command()


class ModernSlider(tk.Frame):
    """Modern volume slider with Canvas - thick bar with speaker icons."""

    def __init__(self, parent, from_=0, to=100, value=80, command=None, **kwargs):
        super().__init__(parent, bg="#1a1a1a")
        self.from_ = from_
        self.to = to
        self.value = value
        self.command = command

        # Main container with icons
        container = tk.Frame(self, bg="#1a1a1a")
        container.pack(fill="x", expand=True)

        # Left speaker icon (mute)
        self.left_icon = tk.Label(
            container, text="🔈", font=("Segoe UI", 14), bg="#1a1a1a", fg="#a1a1aa"
        )
        self.left_icon.pack(side="left", padx=(0, 10))

        # Canvas for slider
        self.canvas = tk.Canvas(
            container,
            width=220,
            height=50,
            bg="#1a1a1a",
            highlightthickness=0,
            cursor="hand2",
        )
        self.canvas.pack(side="left", fill="x", expand=True)

        # Right speaker icon (loud) + percentage
        right_frame = tk.Frame(container, bg="#1a1a1a")
        right_frame.pack(side="left", padx=(10, 0))

        self.right_icon = tk.Label(
            right_frame, text="🔊", font=("Segoe UI", 14), bg="#1a1a1a", fg="#a1a1aa"
        )
        self.right_icon.pack(side="left")

        self.percent_label = tk.Label(
            right_frame,
            text=f"{int(value)}%",
            font=("Segoe UI", 12, "bold"),
            bg="#1a1a1a",
            fg="#22c55e",
            width=4,
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

        # Track settings
        pad = 10
        bar_height = 24  # Thicker bar
        track_y = h // 2
        track_top = track_y - bar_height // 2
        track_bottom = track_y + bar_height // 2
        track_right = w - pad

        # Background track (rounded rectangle effect with overlapping shapes)
        radius = bar_height // 2
        # Main rectangle
        self.canvas.create_rectangle(
            pad + radius,
            track_top,
            track_right - radius,
            track_bottom,
            fill="#404040",
            outline="",
        )
        # Left cap
        self.canvas.create_oval(
            pad, track_top, pad + bar_height, track_bottom, fill="#404040", outline=""
        )
        # Right cap
        self.canvas.create_oval(
            track_right - bar_height,
            track_top,
            track_right,
            track_bottom,
            fill="#404040",
            outline="",
        )

        # Fill (progress)
        ratio = (self.value - self.from_) / max(1, self.to - self.from_)
        fill_width = ratio * (track_right - pad - bar_height)
        fill_x = pad + bar_height // 2 + fill_width

        if ratio > 0.02:  # Only draw if there's something to show
            # Fill rectangle
            self.canvas.create_rectangle(
                pad + radius,
                track_top,
                min(fill_x, track_right - radius),
                track_bottom,
                fill="#22c55e",
                outline="",
            )
            # Fill left cap
            self.canvas.create_oval(
                pad,
                track_top,
                pad + bar_height,
                track_bottom,
                fill="#22c55e",
                outline="",
            )

        # Handle/knob
        handle_x = pad + bar_height // 2 + fill_width
        handle_radius = 14
        self.canvas.create_oval(
            handle_x - handle_radius,
            track_y - handle_radius,
            handle_x + handle_radius,
            track_y + handle_radius,
            fill="white",
            outline="#22c55e",
            width=3,
        )

        # Update percentage label
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
        self.session = None  # Will store session cookie
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
        self,
        method: str,
        path: str,
        *,
        auth_required: bool = True,
        timeout=DEFAULT_TIMEOUT,
        **kwargs,
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
        """
        Login to the API with detailed error handling.

        Returns:
            dict with 'success' (bool) and optional 'error' key:
            - 'invalid_credentials': wrong username/password
            - 'connection_error': cannot reach server
            - 'timeout': request timed out
        """
        try:
            session = requests.Session()
            session.post(
                f"{self.api_base}/login",
                data={"username": username, "password": password},
                allow_redirects=True,
                timeout=LOGIN_TIMEOUT,
            )
            # Check if we got a session cookie (login successful)
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
            # No session cookie = invalid credentials
            return {"success": False, "error": "invalid_credentials"}
        except requests.exceptions.ConnectionError:
            session.close()
            print(f"Connection error: Cannot reach {self.api_base}")
            return {"success": False, "error": "connection_error"}
        except requests.exceptions.Timeout:
            session.close()
            print("Connection timeout")
            return {"success": False, "error": "timeout"}
        except Exception as e:
            session.close()
            print(f"Login error: {e}")
            return {"success": False, "error": "unknown"}

    def discover_server(self, port=5001):
        """Scan local network for AnnounceFlow server on given port.
        Returns the working URL or None.
        """
        # Find local IP to determine subnet
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            local_ip = s.getsockname()[0]
            s.close()
        except Exception:
            return None

        # Get subnet (e.g., 192.168.0)
        parts = local_ip.split(".")
        if len(parts) != 4:
            return None
        subnet = ".".join(parts[:3])

        # Scan common IPs (1-254)
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
                    # Port open, verify it's AnnounceFlow
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
            "POST",
            "/api/play",
            auth_required=True,
            json={"media_id": media_id},
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
            "POST",
            "/api/volume",
            auth_required=True,
            json={"volume": volume},
        )
        return bool(response and response.ok)

    def upload_file(self, filepath, media_type="announcement"):
        """Upload a media file."""
        try:
            with open(filepath, "rb") as f:
                files = {"file": (os.path.basename(filepath), f)}
                data = {"media_type": media_type}
                response = self._request(
                    "POST",
                    "/api/media/upload",
                    auth_required=True,
                    timeout=UPLOAD_TIMEOUT,
                    files=files,
                    data=data,
                )
            return bool(response and response.ok)
        except Exception as e:
            print(f"Upload error: {e}")
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

    def __init__(self, agent):
        self.agent = agent
        self.root: Optional[tk.Tk] = None
        self.logged_in = False
        self.network_worker: Optional[NetworkWorker] = None
        self._closing = False
        self._volume_update_job = None
        self._pending_volume: Optional[int] = None

    def run(self):
        """Run the GUI application."""
        self.root = tk.Tk()
        self.root.title("AnnounceFlow Agent")
        self.root.geometry("400x700")
        self.root.configure(bg="#1a1a1a")
        self.network_worker = NetworkWorker(self.root)
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

        # Style
        style = ttk.Style()
        style.theme_use("clam")
        style.configure("TButton", padding=10, font=("Segoe UI", 10))
        style.configure(
            "TLabel", background="#1a1a1a", foreground="white", font=("Segoe UI", 10)
        )
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

        user_message = "Ağ işlemi sırasında beklenmeyen bir hata oluştu. Lütfen tekrar deneyin."
        if hasattr(self, "status_label"):
            try:
                if self.status_label.winfo_exists():
                    self.status_label.config(text=user_message, fg="#ef4444")
            except tk.TclError:
                pass

        try:
            messagebox.showerror("Ağ Hatası", user_message)
        except tk.TclError:
            pass

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

        if self.network_worker:
            self.network_worker.shutdown()
            self.network_worker = None

        self.agent.close()

        if self.root and self.root.winfo_exists():
            try:
                self.root.destroy()
            except tk.TclError:
                pass

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
        loading_frame = tk.Frame(self.root, bg="#1a1a1a")
        loading_frame.pack(expand=True)

        tk.Label(
            loading_frame, text="🎵", font=("Segoe UI", 48), bg="#1a1a1a", fg="white"
        ).pack(pady=20)
        tk.Label(
            loading_frame,
            text="AnnounceFlow Agent",
            font=("Segoe UI", 16, "bold"),
            bg="#1a1a1a",
            fg="white",
        ).pack()
        tk.Label(
            loading_frame,
            text="Otomatik giriş yapılıyor...",
            bg="#1a1a1a",
            fg="#f59e0b",
            font=("Segoe UI", 10),
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

        frame = tk.Frame(self.root, bg="#1a1a1a")
        frame.pack(expand=True)

        # Logo
        logo_label = tk.Label(
            frame, text="🎵", font=("Segoe UI", 48), bg="#1a1a1a", fg="white"
        )
        logo_label.pack(pady=20)

        title_label = tk.Label(
            frame,
            text="AnnounceFlow Agent",
            font=("Segoe UI", 16, "bold"),
            bg="#1a1a1a",
            fg="white",
        )
        title_label.pack()

        # Server URL
        url_frame = tk.Frame(frame, bg="#1a1a1a")
        url_frame.pack(pady=20, fill="x", padx=40)

        tk.Label(url_frame, text="Sunucu Adresi:", bg="#1a1a1a", fg="#a1a1aa").pack(
            anchor="w"
        )
        self.url_entry = tk.Entry(url_frame, font=("Segoe UI", 10), width=35)
        self.url_entry.insert(0, self.agent.api_base)
        self.url_entry.pack(fill="x", pady=5)

        # Username
        tk.Label(url_frame, text="Kullanıcı Adı:", bg="#1a1a1a", fg="#a1a1aa").pack(
            anchor="w", pady=(10, 0)
        )
        self.username_entry = tk.Entry(url_frame, font=("Segoe UI", 10), width=35)
        self.username_entry.insert(0, "admin")
        self.username_entry.pack(fill="x", pady=5)

        # Password
        tk.Label(url_frame, text="Şifre:", bg="#1a1a1a", fg="#a1a1aa").pack(
            anchor="w", pady=(10, 0)
        )
        self.password_entry = tk.Entry(
            url_frame, font=("Segoe UI", 10), width=35, show="*"
        )
        self.password_entry.pack(fill="x", pady=5)

        # Remember Me checkbox
        self.remember_var = tk.BooleanVar(value=True)
        remember_frame = tk.Frame(url_frame, bg="#1a1a1a")
        remember_frame.pack(fill="x", pady=(10, 0))

        remember_cb = tk.Checkbutton(
            remember_frame,
            text="Beni Hatırla",
            variable=self.remember_var,
            bg="#1a1a1a",
            fg="#a1a1aa",
            selectcolor="#2a2a2a",
            activebackground="#1a1a1a",
            activeforeground="#a1a1aa",
            font=("Segoe UI", 9),
        )
        remember_cb.pack(anchor="w")

        # Login button
        login_btn = ModernButton(
            frame,
            text="Giriş Yap",
            command=self.do_login,
            bg_color="#6366f1",
            hover_color="#818cf8",
        )
        login_btn.pack(pady=20, fill="x", padx=40)

        self.status_label = tk.Label(frame, text="", bg="#1a1a1a", fg="#ef4444")
        self.status_label.pack()

        # Show error message if provided (e.g., password changed)
        if error_message:
            self.status_label.config(text=error_message, fg="#f59e0b")

    def do_login(self):
        """Handle login."""
        url = self.url_entry.get().strip().rstrip("/")
        username = self.username_entry.get().strip()
        password = self.password_entry.get()
        remember = self.remember_var.get()

        self.agent.api_base = url
        self.agent.config["api_base"] = url
        save_agent_config(self.agent.config)

        self.status_label.config(text="Bağlanılıyor...", fg="#f59e0b")

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
                    text="Giriş başarısız! Bilgileri kontrol edin.", fg="#ef4444"
                )
                return
            if error == "connection_error":
                self.status_label.config(text="Sunucu agda bulunamadi!", fg="#ef4444")
                return
            if error == "timeout":
                self.status_label.config(
                    text="Bağlantı zaman aşımına uğradı!", fg="#ef4444"
                )
                return
            self.status_label.config(text="Beklenmeyen bir hata oluştu.", fg="#ef4444")

        self._submit_network_job(_job, on_success=_on_done)

    def show_main_frame(self):
        """Show main control panel."""
        self.clear_frame()

        # Header
        header = tk.Frame(self.root, bg="#262626", pady=15)
        header.pack(fill="x")

        tk.Label(
            header,
            text="🎵 AnnounceFlow",
            font=("Segoe UI", 14, "bold"),
            bg="#262626",
            fg="white",
        ).pack()
        tk.Label(
            header,
            text=f"Bağlı: {self.agent.api_base}",
            font=("Segoe UI", 9),
            bg="#262626",
            fg="#a1a1aa",
        ).pack()

        # Main content
        content = tk.Frame(self.root, bg="#1a1a1a", padx=20, pady=20)
        content.pack(fill="both", expand=True)

        # Default volume; server sync runs asynchronously.
        current_vol = 80

        # Quick Actions

        tk.Label(
            content,
            text="Hızlı İşlemler",
            font=("Segoe UI", 12, "bold"),
            bg="#1a1a1a",
            fg="white",
        ).pack(anchor="w", pady=(0, 10))

        btn_frame = tk.Frame(content, bg="#1a1a1a")
        btn_frame.pack(fill="x", pady=10)

        # Colored buttons for better visibility
        btn_configs = [
            ("🎵 Müzikleri Başlat", self.start_music, "#22c55e", "#4ade80"),
            ("⏹️ Durdur", self.stop_music, "#ef4444", "#f87171"),
            ("📤 Anons Yükle", self.upload_announcement, "#6366f1", "#818cf8"),
            ("🌐 Web Panel", self.open_web_panel, "#3b82f6", "#60a5fa"),
        ]

        for text, command, bg_color, hover_color in btn_configs:
            btn = ModernButton(
                btn_frame,
                text=text,
                command=command,
                bg_color=bg_color,
                hover_color=hover_color,
            )
            btn.pack(fill="x", pady=8)

        # Volume Control
        tk.Label(
            content,
            text="Ses Seviyesi",
            font=("Segoe UI", 12, "bold"),
            bg="#1a1a1a",
            fg="white",
        ).pack(anchor="w", pady=(25, 10))

        # Modern volume slider with icons
        self.volume_slider = ModernSlider(
            content, from_=0, to=100, value=current_vol, command=self.on_volume_change
        )
        self.volume_slider.pack(fill="x", pady=(0, 10))

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

        # Logout (Modern)
        ModernButton(
            content,
            text="Çıkış Yap",
            command=self.logout,
            bg_color="#ef4444",
            hover_color="#f87171",
        ).pack(fill="x", pady=(40, 0))

    def start_music(self):
        """Start background music playlist (loop)."""
        def _on_done(success):
            if not self._root_alive():
                return
            if success:
                messagebox.showinfo("Başarılı", "Arka plan müzik başlatıldı!")
            else:
                messagebox.showerror("Hata", "Müzik başlatılamadı.")

        self._submit_network_job(
            lambda: self.agent.start_playlist(),
            on_success=_on_done,
        )

    def stop_music(self):
        """Stop current playback."""
        def _on_done(success):
            if not self._root_alive():
                return
            if success:
                messagebox.showinfo("Başarılı", "Müzik durduruldu.")
            else:
                messagebox.showerror("Hata", "İşlem başarısız.")

        self._submit_network_job(
            lambda: self.agent.stop_playlist(),
            on_success=_on_done,
        )

    def upload_announcement(self):
        """Upload an announcement file."""
        filepath = filedialog.askopenfilename(
            title="Anons Dosyası Seç",
            filetypes=[("Audio Files", "*.mp3 *.wav *.ogg"), ("All Files", "*.*")],
        )

        if filepath:
            def _on_done(success):
                if not self._root_alive():
                    return
                if success:
                    messagebox.showinfo("Başarılı", "Anons dosyası yüklendi!")
                else:
                    messagebox.showerror("Hata", "Dosya yüklenemedi.")

            self._submit_network_job(
                lambda: self.agent.upload_file(filepath, "announcement"),
                on_success=_on_done,
            )

    def open_web_panel(self):
        """Open web panel in browser."""
        webbrowser.open(self.agent.api_base)

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
        # Clear stored credentials on logout
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
