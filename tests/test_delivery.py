"""Unit tests for delivery logic using mocked HTTP responses."""

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from integrations_hub.models.tables import DeliveryStatus, EventType
from integrations_hub.services.delivery import deliver_webhook


@dataclass
class FakeEvent:
    id: uuid.UUID = field(default_factory=uuid.uuid4)
    event_type: EventType = EventType.request_submitted
    payload: str = field(default_factory=lambda: json.dumps({"title": "Test", "requester": "alice"}))
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class FakeSubscription:
    id: uuid.UUID = field(default_factory=uuid.uuid4)
    url: str = "https://example.com/webhook"
    secret: str = "test-secret-at-least-16-chars"
    enabled: bool = True
    events: str = "request_submitted"


@pytest.mark.asyncio
async def test_deliver_webhook_success():
    event = FakeEvent()
    sub = FakeSubscription()

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.text = "OK"

    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_client.post.return_value = mock_response

    mock_session = AsyncMock()
    mock_session.execute.return_value = MagicMock()
    mock_session.execute.return_value.scalars.return_value.all.return_value = []

    result = await deliver_webhook(mock_session, event, sub, mock_client)

    assert result is True
    mock_client.post.assert_called_once()
    call_kwargs = mock_client.post.call_args
    assert "X-Webhook-Signature" in call_kwargs.kwargs["headers"]
    assert "X-Webhook-Timestamp" in call_kwargs.kwargs["headers"]


@pytest.mark.asyncio
async def test_deliver_webhook_failure():
    event = FakeEvent()
    sub = FakeSubscription()

    mock_response = MagicMock()
    mock_response.status_code = 500
    mock_response.text = "Internal Server Error"

    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_client.post.return_value = mock_response

    mock_session = AsyncMock()
    mock_session.execute.return_value = MagicMock()
    mock_session.execute.return_value.scalars.return_value.all.return_value = []

    with patch("integrations_hub.services.delivery.settings") as mock_settings:
        mock_settings.delivery_max_attempts = 5
        mock_settings.delivery_backoff_base_seconds = 2.0
        mock_settings.delivery_timeout_seconds = 10.0
        result = await deliver_webhook(mock_session, event, sub, mock_client)

    assert result is False


@pytest.mark.asyncio
async def test_deliver_webhook_timeout():
    event = FakeEvent()
    sub = FakeSubscription()

    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_client.post.side_effect = httpx.TimeoutException("timeout")

    mock_session = AsyncMock()
    mock_session.execute.return_value = MagicMock()
    mock_session.execute.return_value.scalars.return_value.all.return_value = []

    with patch("integrations_hub.services.delivery.settings") as mock_settings:
        mock_settings.delivery_max_attempts = 5
        mock_settings.delivery_backoff_base_seconds = 2.0
        mock_settings.delivery_timeout_seconds = 10.0
        result = await deliver_webhook(mock_session, event, sub, mock_client)

    assert result is False
