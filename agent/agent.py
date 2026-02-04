"""
AnnounceFlow - Windows Agent
System tray application for quick access and management.
"""
import os
import json
import webbrowser
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from typing import Optional, Dict, Any
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
                timeout=10,
            )
            # Check if we got a session cookie (login successful)
            if "session" in session.cookies:
                self.session = session
                return {"success": True}
            # No session cookie = invalid credentials
            return {"success": False, "error": "invalid_credentials"}
        except requests.exceptions.ConnectionError:
            print(f"Connection error: Cannot reach {self.api_base}")
            return {"success": False, "error": "connection_error"}
        except requests.exceptions.Timeout:
            print("Connection timeout")
            return {"success": False, "error": "timeout"}
        except Exception as e:
            print(f"Login error: {e}")
            return {"success": False, "error": "unknown"}

    def get_media_files(self):
        """Fetch all media files."""
        if not self.session:
            return []
        try:
            # We need an API endpoint that returns JSON
            # For now, parse from library page or create a simple endpoint
            response = self.session.get(f"{self.api_base}/api/now-playing")
            return response.json() if response.ok else []
        except requests.exceptions.RequestException:
            return []

    def get_health(self):
        """Fetch system health including current volume (no auth required)."""
        try:
            # Use requests directly (no session needed - no auth)
            response = requests.get(f"{self.api_base}/api/health", timeout=5)
            return response.json() if response.ok else {}
        except requests.exceptions.RequestException:
            return {}

    def play_file(self, media_id):
        """Play a media file."""
        if not self.session:
            return False
        try:
            response = self.session.post(
                f"{self.api_base}/api/play", json={"media_id": media_id}
            )
            return response.ok
        except requests.exceptions.RequestException:
            return False

    def stop_playback(self):
        """Stop playback."""
        if not self.session:
            return False
        try:
            response = self.session.post(f"{self.api_base}/api/stop")
            return response.ok
        except requests.exceptions.RequestException:
            return False

    def start_playlist(self):
        """Start background music playlist (loop)."""
        if not self.session:
            return False
        try:
            response = self.session.post(f"{self.api_base}/api/playlist/start-all")
            return response.ok
        except requests.exceptions.RequestException:
            return False

    def stop_playlist(self):
        """Stop background music playlist."""
        if not self.session:
            return False
        try:
            response = self.session.post(f"{self.api_base}/api/playlist/stop")
            return response.ok
        except requests.exceptions.RequestException:
            return False

    def set_volume(self, volume):
        """Set volume level."""
        if not self.session:
            return False
        try:
            response = self.session.post(
                f"{self.api_base}/api/volume", json={"volume": volume}
            )
            return response.ok
        except requests.exceptions.RequestException:
            return False

    def upload_file(self, filepath, media_type="announcement"):
        """Upload a media file."""
        if not self.session:
            return False
        try:
            with open(filepath, "rb") as f:
                files = {"file": (os.path.basename(filepath), f)}
                data = {"media_type": media_type}
                response = self.session.post(
                    f"{self.api_base}/api/media/upload", files=files, data=data
                )
            return response.ok
        except Exception as e:
            print(f"Upload error: {e}")
            return False


class AgentGUI:
    """GUI for the agent."""

    def __init__(self, agent):
        self.agent = agent
        self.root: Optional[tk.Tk] = None
        self.logged_in = False

    def run(self):
        """Run the GUI application."""
        self.root = tk.Tk()
        self.root.title("AnnounceFlow Agent")
        self.root.geometry("400x700")
        self.root.configure(bg="#1a1a1a")

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

        self.root.mainloop()

    def _try_auto_login(self):
        """Attempt auto-login with saved credentials."""
        if has_credentials(self.agent.api_base):
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
            status_label = tk.Label(
                loading_frame,
                text="Otomatik giriş yapılıyor...",
                bg="#1a1a1a",
                fg="#f59e0b",
                font=("Segoe UI", 10),
            )
            status_label.pack(pady=20)
            self.root.update()

            # Get saved credentials and try login
            creds = get_credentials(self.agent.api_base)
            if creds:
                username, password = creds
                result = self.agent.login(username, password)

                if result.get("success"):
                    self.logged_in = True
                    self.show_main_frame()
                    return
                elif result.get("error") == "invalid_credentials":
                    # Password changed on server - clear stored credentials
                    delete_credentials(self.agent.api_base)
                    self.show_login_frame(
                        error_message="Şifreniz değişti, lütfen tekrar girin."
                    )
                    return
                else:
                    # Connection error - show login with message
                    self.show_login_frame(error_message="Sunucuya bağlanılamadı.")
                    return

        # No saved credentials - show normal login
        self.show_login_frame()

    def show_login_frame(self, error_message: str = None):
        """Show login screen."""
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
        if self.root:
            self.root.update()

        result = self.agent.login(username, password)

        if result.get("success"):
            # Save credentials if "Remember Me" is checked
            if remember:
                save_credentials(url, username, password)
            else:
                # Clear any previously saved credentials
                delete_credentials(url)

            self.logged_in = True
            self.show_main_frame()
        else:
            # Show appropriate error message
            error = result.get("error", "unknown")
            if error == "invalid_credentials":
                self.status_label.config(
                    text="Giriş başarısız! Bilgileri kontrol edin.", fg="#ef4444"
                )
            elif error == "connection_error":
                self.status_label.config(text="Sunucuya bağlanılamıyor!", fg="#ef4444")
            elif error == "timeout":
                self.status_label.config(
                    text="Bağlantı zaman aşımına uğradı!", fg="#ef4444"
                )
            else:
                self.status_label.config(
                    text="Beklenmeyen bir hata oluştu.", fg="#ef4444"
                )

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

        # Sync with server immediately (using /api/health - no auth required)
        try:
            health = self.agent.get_health()
            current_vol = health.get("player", {}).get("volume", 80)
        except Exception:
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
        if self.agent.start_playlist():
            messagebox.showinfo("Başarılı", "Arka plan müzik başlatıldı!")
        else:
            messagebox.showerror("Hata", "Müzik başlatılamadı.")

    def stop_music(self):
        """Stop current playback."""
        if self.agent.stop_playlist():
            messagebox.showinfo("Başarılı", "Müzik durduruldu.")
        else:
            messagebox.showerror("Hata", "İşlem başarısız.")

    def upload_announcement(self):
        """Upload an announcement file."""
        filepath = filedialog.askopenfilename(
            title="Anons Dosyası Seç",
            filetypes=[("Audio Files", "*.mp3 *.wav *.ogg"), ("All Files", "*.*")],
        )

        if filepath:
            if self.agent.upload_file(filepath, "announcement"):
                messagebox.showinfo("Başarılı", "Anons dosyası yüklendi!")
            else:
                messagebox.showerror("Hata", "Dosya yüklenemedi.")

    def open_web_panel(self):
        """Open web panel in browser."""
        webbrowser.open(self.agent.api_base)

    def on_volume_change(self, value):
        """Handle volume change."""
        vol = int(float(value))
        self.agent.set_volume(vol)

    def logout(self):
        """Logout and return to login screen."""
        # Clear stored credentials on logout
        delete_credentials(self.agent.api_base)
        self.agent.session = None
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
