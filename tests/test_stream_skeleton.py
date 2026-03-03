"""Stream skeleton contract tests (Faz 2B)."""

from __future__ import annotations

import importlib

from web_panel import app
from services.stream_service import StreamService, StreamStatus
from services.stream_policy import (
    should_force_stop_stream,
    should_interrupt_for_announcement,
    should_resume_stream,
    should_skip_scheduled_music,
)


def test_stream_skeleton_modules_importable():
    modules = [
        "stream_manager",
        "services.stream_service",
        "services.stream_policy",
        "routes.stream_routes",
        "agent.stream_client",
    ]
    for module_name in modules:
        importlib.import_module(module_name)


def test_stream_status_contract_keys():
    payload = StreamStatus().to_dict()
    assert set(payload.keys()) == {
        "active",
        "state",
        "source_before_stream",
        "last_error",
    }
    assert payload["state"] == "idle"
    assert payload["active"] is False


def test_stream_service_stub_contract_shape():
    service = StreamService()
    start_result = service.start()
    stop_result = service.stop()
    status_result = service.status()

    assert set(start_result.keys()) == {"success", "status"}
    assert set(stop_result.keys()) == {"success", "status"}
    assert start_result["success"] is False
    assert stop_result["success"] is False
    assert set(status_result.keys()) == {
        "active",
        "state",
        "source_before_stream",
        "last_error",
    }


def test_stream_policy_functions_return_bool():
    assert isinstance(should_interrupt_for_announcement(True), bool)
    assert isinstance(should_skip_scheduled_music(True), bool)
    assert isinstance(should_force_stop_stream(True), bool)
    assert isinstance(should_resume_stream(True, True), bool)


def test_stream_routes_require_login():
    app.config["TESTING"] = True
    client = app.test_client()

    response = client.get("/api/stream/status", follow_redirects=False)
    assert response.status_code in (301, 302)
    location = response.headers.get("Location", "")
    assert "/login" in location


def test_stream_status_route_contract_when_logged_in():
    app.config["TESTING"] = True
    client = app.test_client()
    with client.session_transaction() as sess:
        sess["logged_in"] = True

    response = client.get("/api/stream/status")
    assert response.status_code == 200
    payload = response.get_json()
    assert set(payload.keys()) == {
        "active",
        "state",
        "source_before_stream",
        "last_error",
    }
    assert payload["state"] == "idle"


def test_stream_start_stop_routes_stub_not_implemented_when_logged_in():
    app.config["TESTING"] = True
    client = app.test_client()
    with client.session_transaction() as sess:
        sess["logged_in"] = True

    start_resp = client.post("/api/stream/start")
    stop_resp = client.post("/api/stream/stop")

    assert start_resp.status_code == 501
    assert stop_resp.status_code == 501
    assert start_resp.get_json().get("error") == "not_implemented"
    assert stop_resp.get_json().get("error") == "not_implemented"
