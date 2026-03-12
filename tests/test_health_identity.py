from web_panel import app


def test_health_includes_identity():
    app.config["TESTING"] = True
    client = app.test_client()

    resp = client.get("/api/health")

    assert resp.status_code == 200
    data = resp.get_json()
    assert isinstance(data, dict)
    identity = data.get("identity")
    assert isinstance(identity, dict)
    assert isinstance(identity.get("instance_id"), str)
    assert identity.get("instance_id")
    assert isinstance(identity.get("site_name"), str)
    assert identity.get("site_name")
