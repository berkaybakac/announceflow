"""
AnnounceFlow - Windows Agent
System tray application for quick access and management.
"""
import os
import sys
import json
import webbrowser
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import requests

# Configuration
API_BASE = "http://aflow.local:5001"
CONFIG_FILE = "agent_config.json"


def load_agent_config():
    """Load agent configuration."""
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, 'r') as f:
            return json.load(f)
    return {"api_base": API_BASE}


def save_agent_config(config):
    """Save agent configuration."""
    with open(CONFIG_FILE, 'w') as f:
        json.dump(config, f, indent=2)


class AnnounceFlowAgent:
    """Main agent application."""
    
    def __init__(self):
        self.config = load_agent_config()
        self.api_base = self.config.get("api_base", API_BASE)
        self.session = None  # Will store session cookie
        
    def login(self, username, password):
        """Login to the API."""
        try:
            session = requests.Session()
            response = session.post(
                f"{self.api_base}/login",
                data={"username": username, "password": password},
                allow_redirects=True,
                timeout=10
            )
            # Check if we got a session cookie (login successful)
            if 'session' in session.cookies:
                self.session = session
                return True
            return False
        except requests.exceptions.ConnectionError:
            print(f"Connection error: Cannot reach {self.api_base}")
            return False
        except requests.exceptions.Timeout:
            print("Connection timeout")
            return False
        except Exception as e:
            print(f"Login error: {e}")
            return False
    
    def get_media_files(self):
        """Fetch all media files."""
        if not self.session:
            return []
        try:
            # We need an API endpoint that returns JSON
            # For now, parse from library page or create a simple endpoint
            response = self.session.get(f"{self.api_base}/api/now-playing")
            return response.json() if response.ok else []
        except:
            return []
    
    def play_file(self, media_id):
        """Play a media file."""
        if not self.session:
            return False
        try:
            response = self.session.post(
                f"{self.api_base}/api/play",
                json={"media_id": media_id}
            )
            return response.ok
        except:
            return False
    
    def stop_playback(self):
        """Stop playback."""
        if not self.session:
            return False
        try:
            response = self.session.post(f"{self.api_base}/api/stop")
            return response.ok
        except:
            return False
    
    def set_volume(self, volume):
        """Set volume level."""
        if not self.session:
            return False
        try:
            response = self.session.post(
                f"{self.api_base}/api/volume",
                json={"volume": volume}
            )
            return response.ok
        except:
            return False
    
    def upload_file(self, filepath, media_type="announcement"):
        """Upload a media file."""
        if not self.session:
            return False
        try:
            with open(filepath, 'rb') as f:
                files = {'file': (os.path.basename(filepath), f)}
                data = {'media_type': media_type}
                response = self.session.post(
                    f"{self.api_base}/api/media/upload",
                    files=files,
                    data=data
                )
            return response.ok
        except Exception as e:
            print(f"Upload error: {e}")
            return False


class AgentGUI:
    """GUI for the agent."""
    
    def __init__(self, agent):
        self.agent = agent
        self.root = None
        self.logged_in = False
        
    def run(self):
        """Run the GUI application."""
        self.root = tk.Tk()
        self.root.title("AnnounceFlow Agent")
        self.root.geometry("400x500")
        self.root.configure(bg="#1a1a1a")
        
        # Style
        style = ttk.Style()
        style.theme_use('clam')
        style.configure("TButton", padding=10, font=('Segoe UI', 10))
        style.configure("TLabel", background="#1a1a1a", foreground="white", font=('Segoe UI', 10))
        style.configure("TEntry", padding=5)
        
        self.show_login_frame()
        
        self.root.mainloop()
    
    def show_login_frame(self):
        """Show login screen."""
        self.clear_frame()
        
        frame = tk.Frame(self.root, bg="#1a1a1a")
        frame.pack(expand=True)
        
        # Logo
        logo_label = tk.Label(frame, text="🎵", font=('Segoe UI', 48), bg="#1a1a1a", fg="white")
        logo_label.pack(pady=20)
        
        title_label = tk.Label(frame, text="AnnounceFlow Agent", font=('Segoe UI', 16, 'bold'), 
                              bg="#1a1a1a", fg="white")
        title_label.pack()
        
        # Server URL
        url_frame = tk.Frame(frame, bg="#1a1a1a")
        url_frame.pack(pady=20, fill='x', padx=40)
        
        tk.Label(url_frame, text="Sunucu Adresi:", bg="#1a1a1a", fg="#a1a1aa").pack(anchor='w')
        self.url_entry = tk.Entry(url_frame, font=('Segoe UI', 10), width=35)
        self.url_entry.insert(0, self.agent.api_base)
        self.url_entry.pack(fill='x', pady=5)
        
        # Username
        tk.Label(url_frame, text="Kullanıcı Adı:", bg="#1a1a1a", fg="#a1a1aa").pack(anchor='w', pady=(10,0))
        self.username_entry = tk.Entry(url_frame, font=('Segoe UI', 10), width=35)
        self.username_entry.insert(0, "admin")
        self.username_entry.pack(fill='x', pady=5)
        
        # Password
        tk.Label(url_frame, text="Şifre:", bg="#1a1a1a", fg="#a1a1aa").pack(anchor='w', pady=(10,0))
        self.password_entry = tk.Entry(url_frame, font=('Segoe UI', 10), width=35, show="*")
        self.password_entry.pack(fill='x', pady=5)
        
        # Login button
        login_btn = tk.Button(frame, text="Giriş Yap", font=('Segoe UI', 11, 'bold'),
                             bg="#6366f1", fg="white", padx=30, pady=10,
                             command=self.do_login, cursor="hand2",
                             relief='flat', activebackground="#818cf8")
        login_btn.pack(pady=20)
        
        self.status_label = tk.Label(frame, text="", bg="#1a1a1a", fg="#ef4444")
        self.status_label.pack()
    
    def do_login(self):
        """Handle login."""
        url = self.url_entry.get().strip().rstrip('/')
        username = self.username_entry.get().strip()
        password = self.password_entry.get()
        
        self.agent.api_base = url
        self.agent.config["api_base"] = url
        save_agent_config(self.agent.config)
        
        self.status_label.config(text="Bağlanılıyor...", fg="#f59e0b")
        self.root.update()
        
        if self.agent.login(username, password):
            self.logged_in = True
            self.show_main_frame()
        else:
            self.status_label.config(text="Giriş başarısız! Bilgileri kontrol edin.", fg="#ef4444")
    
    def show_main_frame(self):
        """Show main control panel."""
        self.clear_frame()
        
        # Header
        header = tk.Frame(self.root, bg="#262626", pady=15)
        header.pack(fill='x')
        
        tk.Label(header, text="🎵 AnnounceFlow", font=('Segoe UI', 14, 'bold'),
                bg="#262626", fg="white").pack()
        tk.Label(header, text=f"Bağlı: {self.agent.api_base}", font=('Segoe UI', 9),
                bg="#262626", fg="#a1a1aa").pack()
        
        # Main content
        content = tk.Frame(self.root, bg="#1a1a1a", padx=20, pady=20)
        content.pack(fill='both', expand=True)
        
        # Quick Actions
        tk.Label(content, text="Hızlı İşlemler", font=('Segoe UI', 12, 'bold'),
                bg="#1a1a1a", fg="white").pack(anchor='w', pady=(0,10))
        
        btn_frame = tk.Frame(content, bg="#1a1a1a")
        btn_frame.pack(fill='x', pady=10)
        
        # Colored buttons for better visibility
        btn_configs = [
            ("📤 Anons Yükle", self.upload_announcement, "#6366f1", "#818cf8"),
            ("🌐 Web Panel", self.open_web_panel, "#3b82f6", "#60a5fa"),
            ("⏹️ Durdur", self.stop_playback, "#ef4444", "#f87171"),
        ]
        
        for text, command, bg_color, hover_color in btn_configs:
            btn = tk.Button(btn_frame, text=text, font=('Segoe UI', 11, 'bold'),
                           bg=bg_color, fg="white", padx=20, pady=15,
                           command=command, relief='flat', cursor="hand2",
                           activebackground=hover_color, activeforeground="white")
            btn.pack(fill='x', pady=5)
        
        # Volume Control
        tk.Label(content, text="Ses Seviyesi", font=('Segoe UI', 12, 'bold'),
                bg="#1a1a1a", fg="white").pack(anchor='w', pady=(20,10))
        
        vol_frame = tk.Frame(content, bg="#1a1a1a")
        vol_frame.pack(fill='x')
        
        self.volume_var = tk.IntVar(value=80)
        self.volume_label = tk.Label(vol_frame, text="80%", font=('Segoe UI', 12, 'bold'),
                                     bg="#1a1a1a", fg="#22c55e", width=5)
        self.volume_label.pack(side='right')
        
        self.volume_scale = tk.Scale(vol_frame, from_=0, to=100, orient='horizontal',
                                     variable=self.volume_var, command=self.on_volume_change,
                                     bg="#1a1a1a", fg="white", troughcolor="#374151",
                                     highlightthickness=0, sliderrelief='flat',
                                     activebackground="#22c55e", length=250)
        self.volume_scale.pack(fill='x', side='left', expand=True)
        
        # Logout
        tk.Button(content, text="🚪 Çıkış", font=('Segoe UI', 10),
                 bg="#ef4444", fg="white", command=self.logout,
                 relief='flat', cursor="hand2", pady=8).pack(fill='x', pady=(30,0))
    
    def upload_announcement(self):
        """Upload an announcement file."""
        filepath = filedialog.askopenfilename(
            title="Anons Dosyası Seç",
            filetypes=[("Audio Files", "*.mp3 *.wav *.ogg"), ("All Files", "*.*")]
        )
        
        if filepath:
            if self.agent.upload_file(filepath, "announcement"):
                messagebox.showinfo("Başarılı", "Anons dosyası yüklendi!")
            else:
                messagebox.showerror("Hata", "Dosya yüklenemedi.")
    
    def open_web_panel(self):
        """Open web panel in browser."""
        webbrowser.open(self.agent.api_base)
    
    def stop_playback(self):
        """Stop current playback."""
        if self.agent.stop_playback():
            messagebox.showinfo("Başarılı", "Oynatma durduruldu.")
        else:
            messagebox.showerror("Hata", "İşlem başarısız.")
    
    def on_volume_change(self, value):
        """Handle volume change."""
        vol = int(float(value))
        self.volume_label.config(text=f"{vol}%")
        self.agent.set_volume(vol)
    
    def logout(self):
        """Logout and return to login screen."""
        self.agent.session = None
        self.logged_in = False
        self.show_login_frame()
    
    def clear_frame(self):
        """Clear all widgets from root."""
        for widget in self.root.winfo_children():
            widget.destroy()


def main():
    """Main entry point."""
    agent = AnnounceFlowAgent()
    gui = AgentGUI(agent)
    gui.run()


if __name__ == "__main__":
    main()
