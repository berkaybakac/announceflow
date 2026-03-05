"""
AnnounceFlow Agent - PyInstaller Build Script
Creates a standalone Windows executable.
"""
import subprocess
import sys
import os

def build():
    """Build the agent executable."""
    agent_path = os.path.join(os.path.dirname(__file__), "agent.py")
    config_data = (
        "agent_config.json;." if os.name == "nt" else "agent_config.json:."
    )

    sep = ";" if os.name == "nt" else ":"

    # PyInstaller command
    cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--clean",  # Build from a clean state
        "--onefile",  # Single executable
        "--noconsole",  # No console window (GUI app)
        "--name", "AnnounceFlowAgent",
        "--add-data",
        config_data,
        # Keyring backend modules are resolved dynamically; include all submodules.
        "--collect-submodules",
        "keyring",
        "--hidden-import",
        "keyring.backends.Windows",
        "--hidden-import",
        "keyring.errors",
        # soundcard uses CFFI; ensure it's bundled correctly
        "--hidden-import",
        "soundcard",
        "--hidden-import",
        "numpy",
        # Uncomment if you have an icon:
        # "--icon", "icon.ico",
        agent_path,
    ]

    # Bundle PNG icons if available
    icons_dir = os.path.join(os.path.dirname(__file__), "assets", "icons")
    if os.path.isdir(icons_dir):
        icons_data = f"{icons_dir}{sep}assets/icons"
        cmd.insert(-1, "--add-data")
        cmd.insert(-1, icons_data)

    print("Building AnnounceFlow Agent...")
    print(f"Command: {' '.join(cmd)}")
    print("-" * 50)

    try:
        subprocess.run(cmd, check=True)
        print("-" * 50)
        print("Build complete!")
        print("Executable location: dist/AnnounceFlowAgent.exe")
    except subprocess.CalledProcessError as e:
        print(f"Build failed: {e}")
        sys.exit(1)
    except FileNotFoundError:
        print("PyInstaller not found. Install with: pip install pyinstaller")
        sys.exit(1)


if __name__ == "__main__":
    build()
