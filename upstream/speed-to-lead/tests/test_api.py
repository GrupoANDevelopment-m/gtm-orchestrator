"""API surface: health, sync run, webhook signature, metrics."""

from fastapi.testclient import TestClient

from speed_to_lead.api import app
from speed_to_lead.api.security import sign


def test_health() -> None:
    with TestClient(app) as client:
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"


def test_sync_run_returns_outcome() -> None:
    with TestClient(app) as client:
        resp = client.post(
            "/leads/sync",
            json={"email": "maria@acme.com", "message": "need pricing for a demo", "source": "ads"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["qualification"]["tier"] == "hot"
        assert body["lead"]["source"] == "ads"


def test_webhook_enqueues_with_202() -> None:
    with TestClient(app) as client:
        resp = client.post("/leads", json={"email": "x@acme.com", "message": "hi"})
        assert resp.status_code == 202
        assert resp.json()["status"] == "queued"


def test_metrics_endpoint() -> None:
    with TestClient(app) as client:
        client.post("/leads/sync", json={"email": "a@acme.com", "message": "pricing please"})
        snap = client.get("/metrics").json()
        assert snap["leads_total"] >= 1
        assert "by_source" in snap  # attribution present


def test_signature_helper_roundtrip() -> None:
    body = b'{"email":"a@b.com"}'
    assert sign("secret", body) == sign("secret", body)
    assert sign("secret", body) != sign("other", body)


def test_oversized_message_is_rejected() -> None:
    with TestClient(app) as client:
        resp = client.post("/leads/sync", json={"email": "a@acme.com", "message": "x" * 6000})
        assert resp.status_code == 422  # length cap enforced


def test_malformed_webhook_returns_400_not_500() -> None:
    with TestClient(app) as client:
        resp = client.post("/leads", json={"name": "no email provided"})
        assert resp.status_code == 400
