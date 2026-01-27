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
    
    # PyInstaller command
    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--onefile",           # Single executable
        "--noconsole",         # No console window (GUI app)
        "--name", "AnnounceFlowAgent",
        "--add-data", "agent_config.json;." if os.name == 'nt' else "agent_config.json:.",
        # Uncomment if you have an icon:
        # "--icon", "icon.ico",
        agent_path
    ]
    
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
