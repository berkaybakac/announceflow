# Deployment and Operations

## Development

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python main.py
```

## Production (Raspberry Pi)

```bash
# Standard deploy (dev/test)
./deploy.sh stateksound.local

# Customer delivery deploy (clean content)
DEPLOY_PROFILE=clean-delivery ./deploy.sh stateksound.local
```

### Deploy Profiles

- `standard`: routine deploy for development and regression validation.
- `clean-delivery`: handoff-only deploy. Clears existing `media/`, `logs/`, `runtime/`, and local `*.db` files on target before first customer upload.

### Deploy Pipeline Details

The `deploy.sh` script handles end-to-end deployment:

1. Rsync project files to Pi (excludes `.git`, `venv`, `.env`, `config.json`, logs, databases).
2. (clean-delivery only) Sanitize media/logs/runtime directories.
3. Upload release stamp (commit hash, ref, branch, UTC deploy timestamp).
4. Generate Flask secret key if missing, protect `.env` permissions.
5. Install system dependencies (`mpg123`, `ffmpeg`, `alsa-utils`).
6. Install Python dependencies (filters out desktop-only packages for Pi).
7. Create and enable systemd service with auto-restart policy.
8. Post-deploy health check: retries `/api/health` up to 15 times (2s intervals), validates player backend and scheduler state.

SSH multiplexing is used to avoid repeated password prompts.

## Hostname Standard (Single Branch = Single Pi)

- Primary endpoint for panel and agent: `http://stateksound.local:5001`
- Hostname-first is the default operational model.
- If hostname resolution fails on-site, use router/ARP-discovered Pi IP as temporary fallback.

## Windows Agent Distribution Flow

1. Put the latest EXE at `agent/releases/StatekSound.exe`.
2. After deployment, technical staff downloads EXE from panel (`/downloads/agent/latest`).
3. First Windows login should use `http://stateksound.local:5001`.

## Release Workflow

1. Build/update `StatekSound.exe`.
2. Place EXE under `agent/releases/StatekSound.exe`.
3. Run standard deploy (`./deploy.sh stateksound.local`).
4. Run test gate (`python -m pytest -q`).
5. Validate panel health and agent download path.
6. Commit, tag, release notes.
