import pytest
from pydantic import ValidationError

from integrations_hub.schemas.subscriptions import SubscriptionCreate, SubscriptionResponse


def test_subscription_create_valid():
    data = SubscriptionCreate(
        url="https://example.com/webhook",
        secret="a-long-enough-secret",
        events=["request_submitted", "request_approved"],
    )
    assert str(data.url) == "https://example.com/webhook"
    assert data.events == ["request_submitted", "request_approved"]


def test_subscription_create_invalid_event():
    with pytest.raises(ValidationError, match="Invalid event type"):
        SubscriptionCreate(
            url="https://example.com/webhook",
            secret="a-long-enough-secret",
            events=["invalid_event"],
        )


def test_subscription_create_empty_events():
    with pytest.raises(ValidationError, match="At least one event type"):
        SubscriptionCreate(
            url="https://example.com/webhook",
            secret="a-long-enough-secret",
            events=[],
        )


def test_subscription_create_short_secret():
    with pytest.raises(ValidationError, match="at least 16 characters"):
        SubscriptionCreate(
            url="https://example.com/webhook",
            secret="short",
            events=["request_submitted"],
        )


def test_subscription_response_splits_events():
    resp = SubscriptionResponse(
        id="00000000-0000-0000-0000-000000000001",
        url="https://example.com/webhook",
        events="request_submitted,request_approved",
        enabled=True,
        created_at="2024-01-01T00:00:00Z",
        updated_at="2024-01-01T00:00:00Z",
    )
    assert resp.events == ["request_submitted", "request_approved"]
