import json
import os
import pytest
from flask import Flask
from routes.player_routes import player_bp

@pytest.fixture
def app():
    """Create a mock Flask app for testing diagnostics."""
    app = Flask(__name__)
    app.register_blueprint(player_bp)
    return app

@pytest.fixture
def client(app):
    return app.test_client()

def test_diagnose_api_returns_json(client, tmp_path, monkeypatch):
    """Verify that /api/diagnose returns a valid JSON structure."""
    # 1. Setup a temporary log file
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    log_file = log_dir / "events.jsonl"
    
    # 2. Inject some mock logs
    mock_logs = [
        {"ts": "2026-04-01T12:00:00Z", "event": "system_health", "data": {"temp_c": 55.0, "load_1m": 0.5}},
        {"ts": "2026-04-01T12:05:00Z", "event": "xrun_snapshot", "data": {}},
        {"ts": "2026-04-01T12:10:00Z", "event": "track_end", "data": {}},
    ]
    
    with open(log_file, "w") as f:
        for entry in mock_logs:
            f.write(json.dumps(entry) + "\n")

    # 3. Patch the LOG_FILE path in diagnose module to point to our tmp file
    import diagnose
    monkeypatch.setattr(diagnose, "LOG_FILE", str(log_file))

    # 4. Request the API
    response = client.get("/api/diagnose?minutes=10000") # Large lookback to catch mock logs
    
    # 5. Assertions
    assert response.status_code == 200
    data = response.get_json()
    
    assert "xruns" in data
    assert data["xruns"] == 1
    assert data["tracks_played"] == 1
    assert data["lookback_minutes"] == 10000
    assert len(data["temps"]) == 1
    assert data["temps"][0] == 55.0

def test_diagnose_api_404_when_no_log(client, monkeypatch):
    """Verify that /api/diagnose handles missing log files gracefully."""
    import diagnose
    monkeypatch.setattr(diagnose, "LOG_FILE", "/non/existent/path.jsonl")
    
    response = client.get("/api/diagnose")
    assert response.status_code == 404
    assert "error" in response.get_json()
    assert "not found" in response.get_json()["error"].lower()
