import json

import httpx
import structlog

from integrations_hub.config import settings
from integrations_hub.models.tables import OutboxEvent

logger = structlog.get_logger()

SLACK_POST_MESSAGE_URL = "https://slack.com/api/chat.postMessage"


def format_slack_message(event: OutboxEvent) -> dict:
    """Format an outbox event into a Slack message payload."""
    payload = json.loads(event.payload)
    title = payload.get("title", "New Request")
    requester = payload.get("requester", "Unknown")
    description = payload.get("description", "")

    blocks = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": f"New Request Submitted: {title}"},
        },
        {
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": f"*Requester:*\n{requester}"},
                {"type": "mrkdwn", "text": f"*Event ID:*\n{event.id}"},
            ],
        },
    ]

    if description:
        blocks.append(
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"*Description:*\n{description}"},
            }
        )

    return {
        "channel": settings.slack_default_channel,
        "text": f"New request submitted: {title}",
        "blocks": blocks,
    }


async def send_slack_notification(
    event: OutboxEvent, http_client: httpx.AsyncClient | None = None
) -> bool:
    """Send a Slack notification for a request_submitted event."""
    if not settings.slack_bot_token:
        logger.warning("slack_bot_token_not_configured")
        return False

    message = format_slack_message(event)
    own_client = http_client is None

    if own_client:
        http_client = httpx.AsyncClient()

    try:
        response = await http_client.post(
            SLACK_POST_MESSAGE_URL,
            json=message,
            headers={
                "Authorization": f"Bearer {settings.slack_bot_token}",
                "Content-Type": "application/json",
            },
            timeout=10.0,
        )

        data = response.json()
        if data.get("ok"):
            logger.info(
                "slack_notification_sent",
                event_id=str(event.id),
                channel=settings.slack_default_channel,
            )
            return True
        else:
            logger.error(
                "slack_api_error",
                event_id=str(event.id),
                error=data.get("error"),
            )
            return False
    except httpx.RequestError as exc:
        logger.error("slack_request_error", event_id=str(event.id), error=str(exc))
        return False
    finally:
        if own_client:
            await http_client.aclose()
