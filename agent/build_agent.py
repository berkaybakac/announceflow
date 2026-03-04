"""
AnnounceFlow Agent - PyInstaller Build Script
Creates a standalone Windows executable with bundled ffmpeg.
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

    # ffmpeg binary to bundle (REQUIRED for stream functionality)
    ffmpeg_path = os.path.join(os.path.dirname(__file__), "ffmpeg.exe")
    if not os.path.isfile(ffmpeg_path):
        print("ERROR: ffmpeg.exe not found in agent/ directory.")
        print("Stream functionality requires bundled ffmpeg.")
        print("Download from: https://www.gyan.dev/ffmpeg/builds/")
        sys.exit(1)
    sep = ";" if os.name == "nt" else ":"
    ffmpeg_data = f"{ffmpeg_path}{sep}."

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
        # Uncomment if you have an icon:
        # "--icon", "icon.ico",
        agent_path,
    ]

    # Bundle ffmpeg.exe (required)
    cmd.insert(-1, "--add-binary")
    cmd.insert(-1, ffmpeg_data)

    # Bundle VB-Cable installer if available
    vbcable_path = os.path.join(os.path.dirname(__file__), "VBCABLE_Setup_x64.exe")
    if os.path.isfile(vbcable_path):
        sep = ";" if os.name == "nt" else ":"
        cmd.insert(-1, "--add-binary")
        cmd.insert(-1, f"{vbcable_path}{sep}.")
    
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
