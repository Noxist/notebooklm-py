"""The local web adapter must validate without opening a Google session."""

from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from notebooklm.batch.web import MAX_BODY_BYTES, create_app


@pytest.fixture
def local_client(tmp_path):
    factory = Mock(side_effect=AssertionError("Google must not be contacted"))
    app = create_app(tmp_path / "jobs.sqlite3", client_factory=factory, run_workers=False)
    with TestClient(app, base_url="http://127.0.0.1:8765") as client:
        yield client, factory


def payload():
    return {"jobs": [{"id": "a", "type": "audio", "notebook_id": "book", "title": "Hello"}]}


def post(client, path, body):
    return client.post(path, json=body, headers={"Origin": "http://127.0.0.1:8765"})


def test_editor_schema_and_dry_run_are_offline(local_client):
    client, factory = local_client
    assert client.get("/").status_code == 200
    assert "JSON" in client.get("/").text
    assert client.get("/app.js").status_code == 200
    assert client.get("/api/schema").status_code == 200
    assert post(client, "/api/validate", payload()).status_code == 200
    assert post(client, "/api/dry-run", payload()).json()["dry_run"] is True
    assert client.get("/api/jobs").json() == []
    factory.assert_not_called()


def test_submission_is_persistent_and_idempotent(local_client):
    client, factory = local_client
    assert post(client, "/api/submit", payload()).status_code == 200
    assert post(client, "/api/submit", payload()).status_code == 200
    assert len(client.get("/api/jobs").json()) == 1
    changed = payload()
    changed["jobs"][0]["title"] = "Changed"
    assert post(client, "/api/submit", changed).status_code == 422
    factory.assert_not_called()


def test_cross_origin_and_rebinding_requests_are_rejected(local_client):
    client, _ = local_client
    assert client.post("/api/submit", json=payload()).status_code == 403
    assert (
        client.post(
            "/api/submit", json=payload(), headers={"Origin": "http://evil.example"}
        ).status_code
        == 403
    )
    assert client.get("/api/jobs", headers={"Host": "evil.example"}).status_code == 403
    assert (
        client.post(
            "/api/submit", content="{}", headers={"Origin": "http://127.0.0.1:8765"}
        ).status_code
        == 415
    )


def test_invalid_and_large_json_are_rejected(local_client):
    client, _ = local_client
    headers = {"Origin": "http://127.0.0.1:8765", "Content-Type": "application/json"}
    assert client.post("/api/validate", content="{", headers=headers).status_code == 422
    assert (
        client.post(
            "/api/validate", content="x" * (MAX_BODY_BYTES + 1), headers=headers
        ).status_code
        == 413
    )
    assert post(client, "/api/jobs/a/attach", {}).status_code == 422


def test_invalid_runtime_controls(tmp_path):
    with pytest.raises(ValueError):
        create_app(tmp_path / "jobs.sqlite3", concurrency=0)
