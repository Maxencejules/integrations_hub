import json
import uuid

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from integrations_hub.models.tables import EventType, OutboxEvent

logger = structlog.get_logger()


async def publish_event(
    session: AsyncSession, event_type: str, payload: dict
) -> OutboxEvent:
    """Write an event to the outbox table for async delivery."""
    event = OutboxEvent(
        event_type=EventType(event_type),
        payload=json.dumps(payload),
    )
    session.add(event)
    await session.commit()
    await session.refresh(event)
    logger.info("event_published", event_id=str(event.id), event_type=event_type)
    return event


async def get_event(session: AsyncSession, event_id: uuid.UUID) -> OutboxEvent | None:
    return await session.get(OutboxEvent, event_id)
