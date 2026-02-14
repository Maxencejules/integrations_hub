import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from integrations_hub.connectors.slack import format_slack_message, send_slack_notification
from integrations_hub.models.tables import EventType


@dataclass
class FakeEvent:
    id: uuid.UUID = field(default_factory=uuid.uuid4)
    event_type: EventType = EventType.request_submitted
    payload: str = ""
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


def _make_event(payload: dict | None = None) -> FakeEvent:
    if payload is None:
        payload = {"title": "Test Request", "requester": "alice", "description": "Need access"}
    return FakeEvent(payload=json.dumps(payload))


def test_format_slack_message():
    event = _make_event()
    message = format_slack_message(event)

    assert "channel" in message
    assert "text" in message
    assert "blocks" in message
    assert "Test Request" in message["text"]
    assert len(message["blocks"]) == 3  # header + section + description


def test_format_slack_message_no_description():
    event = _make_event({"title": "Simple", "requester": "bob"})
    message = format_slack_message(event)

    assert len(message["blocks"]) == 2  # header + section only


@pytest.mark.asyncio
async def test_send_slack_notification_no_token():
    event = _make_event()
    with patch("integrations_hub.connectors.slack.settings") as mock_settings:
        mock_settings.slack_bot_token = ""
        result = await send_slack_notification(event)
    assert result is False


@pytest.mark.asyncio
async def test_send_slack_notification_success():
    event = _make_event()
    mock_response = MagicMock()
    mock_response.json.return_value = {"ok": True}

    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_client.post.return_value = mock_response

    with patch("integrations_hub.connectors.slack.settings") as mock_settings:
        mock_settings.slack_bot_token = "xoxb-test-token"
        mock_settings.slack_default_channel = "#test"
        result = await send_slack_notification(event, http_client=mock_client)

    assert result is True
    mock_client.post.assert_called_once()


@pytest.mark.asyncio
async def test_send_slack_notification_api_error():
    event = _make_event()
    mock_response = MagicMock()
    mock_response.json.return_value = {"ok": False, "error": "channel_not_found"}

    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_client.post.return_value = mock_response

    with patch("integrations_hub.connectors.slack.settings") as mock_settings:
        mock_settings.slack_bot_token = "xoxb-test-token"
        mock_settings.slack_default_channel = "#nonexistent"
        result = await send_slack_notification(event, http_client=mock_client)

    assert result is False
