"""Agent build version.

"dev" when run from source. CI overwrites this file before PyInstaller runs
(see .github/workflows/*windows-agent*.yml): "<tag>-<sha7>" for releases,
"main-<sha7>" for main builds. The agent reports it in every heartbeat so
the Pi can tell which EXE each PC runs.
"""

AGENT_VERSION = "dev"
