"""Integration tests that hit the API with a real database session."""

import uuid

import pytest
import pytest_asyncio
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_health(client: AsyncClient):
    response = await client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@pytest.mark.asyncio
async def test_subscription_crud(client: AsyncClient):
    # Create
    create_resp = await client.post(
        "/api/v1/subscriptions",
        json={
            "url": "https://example.com/webhook",
            "secret": "a-long-enough-secret-key",
            "events": ["request_submitted", "request_approved"],
            "enabled": True,
        },
    )
    assert create_resp.status_code == 201
    data = create_resp.json()
    sub_id = data["id"]
    assert data["url"] == "https://example.com/webhook"
    assert data["events"] == ["request_submitted", "request_approved"]
    assert data["enabled"] is True

    # Read
    get_resp = await client.get(f"/api/v1/subscriptions/{sub_id}")
    assert get_resp.status_code == 200
    assert get_resp.json()["id"] == sub_id

    # List
    list_resp = await client.get("/api/v1/subscriptions")
    assert list_resp.status_code == 200
    assert any(s["id"] == sub_id for s in list_resp.json())

    # Update
    update_resp = await client.put(
        f"/api/v1/subscriptions/{sub_id}",
        json={"enabled": False},
    )
    assert update_resp.status_code == 200
    assert update_resp.json()["enabled"] is False

    # Delete
    del_resp = await client.delete(f"/api/v1/subscriptions/{sub_id}")
    assert del_resp.status_code == 204

    # Verify deleted
    get_resp = await client.get(f"/api/v1/subscriptions/{sub_id}")
    assert get_resp.status_code == 404


@pytest.mark.asyncio
async def test_subscription_not_found(client: AsyncClient):
    fake_id = str(uuid.uuid4())
    resp = await client.get(f"/api/v1/subscriptions/{fake_id}")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_create_subscription_validation(client: AsyncClient):
    # Missing required fields
    resp = await client.post("/api/v1/subscriptions", json={})
    assert resp.status_code == 422

    # Invalid event type
    resp = await client.post(
        "/api/v1/subscriptions",
        json={
            "url": "https://example.com/hook",
            "secret": "a-long-enough-secret-key",
            "events": ["invalid_event"],
        },
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_publish_event(client: AsyncClient):
    resp = await client.post(
        "/api/v1/events",
        json={
            "event_type": "request_submitted",
            "payload": {"title": "Test", "requester": "alice"},
        },
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["event_type"] == "request_submitted"
    assert "id" in data


@pytest.mark.asyncio
async def test_publish_event_invalid_type(client: AsyncClient):
    resp = await client.post(
        "/api/v1/events",
        json={
            "event_type": "invalid_type",
            "payload": {"title": "Test"},
        },
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_delivery_attempts_empty(client: AsyncClient):
    fake_id = str(uuid.uuid4())
    resp = await client.get(f"/api/v1/admin/events/{fake_id}/attempts")
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.asyncio
async def test_replay_dead_letter_not_found(client: AsyncClient):
    fake_id = str(uuid.uuid4())
    resp = await client.post(f"/api/v1/admin/dead-letters/{fake_id}/replay")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_metrics_endpoint(client: AsyncClient):
    resp = await client.get("/metrics")
    assert resp.status_code == 200
    assert "webhook_deliveries_total" in resp.text
