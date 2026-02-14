import json
import uuid
from datetime import datetime, timedelta, timezone

import httpx
import structlog
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from integrations_hub.config import settings
from integrations_hub.models.tables import (
    DeadLetter,
    DeliveryAttempt,
    DeliveryStatus,
    OutboxEvent,
    WebhookSubscription,
)
from integrations_hub.services.signing import sign_payload

logger = structlog.get_logger()

DELIVERY_COUNTER_SUCCESS = "webhook_delivery_success_total"
DELIVERY_COUNTER_FAILURE = "webhook_delivery_failure_total"


async def get_pending_deliveries(session: AsyncSession) -> list[OutboxEvent]:
    """Find outbox events that have subscriptions needing delivery."""
    result = await session.execute(
        select(OutboxEvent).order_by(OutboxEvent.created_at.asc()).limit(50)
    )
    return list(result.scalars().all())


async def get_matching_subscriptions(
    session: AsyncSession, event_type: str
) -> list[WebhookSubscription]:
    """Find enabled subscriptions that listen for this event type."""
    result = await session.execute(
        select(WebhookSubscription).where(
            WebhookSubscription.enabled.is_(True),
            WebhookSubscription.events.contains(event_type),
        )
    )
    return list(result.scalars().all())


async def has_been_delivered(
    session: AsyncSession, event_id: uuid.UUID, subscription_id: uuid.UUID
) -> bool:
    """Check idempotency: was this event already successfully delivered to this subscription?"""
    result = await session.execute(
        select(DeliveryAttempt).where(
            DeliveryAttempt.event_id == event_id,
            DeliveryAttempt.subscription_id == subscription_id,
            DeliveryAttempt.status == DeliveryStatus.delivered,
        )
    )
    return result.scalar_one_or_none() is not None


async def is_dead_lettered(
    session: AsyncSession, event_id: uuid.UUID, subscription_id: uuid.UUID
) -> bool:
    """Check if this event/subscription pair is already in the dead letter table."""
    result = await session.execute(
        select(DeadLetter).where(
            DeadLetter.event_id == event_id,
            DeadLetter.subscription_id == subscription_id,
        )
    )
    return result.scalar_one_or_none() is not None


async def get_retry_attempt(
    session: AsyncSession, event_id: uuid.UUID, subscription_id: uuid.UUID
) -> DeliveryAttempt | None:
    """Find the latest pending retry for this event/subscription pair."""
    now = datetime.now(timezone.utc)
    result = await session.execute(
        select(DeliveryAttempt)
        .where(
            DeliveryAttempt.event_id == event_id,
            DeliveryAttempt.subscription_id == subscription_id,
            DeliveryAttempt.status == DeliveryStatus.pending,
            DeliveryAttempt.next_retry_at <= now,
        )
        .order_by(DeliveryAttempt.attempt_number.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def get_attempt_count(
    session: AsyncSession, event_id: uuid.UUID, subscription_id: uuid.UUID
) -> int:
    """Count delivery attempts for this event/subscription pair."""
    result = await session.execute(
        select(DeliveryAttempt).where(
            DeliveryAttempt.event_id == event_id,
            DeliveryAttempt.subscription_id == subscription_id,
        )
    )
    return len(result.scalars().all())


async def deliver_webhook(
    session: AsyncSession,
    event: OutboxEvent,
    subscription: WebhookSubscription,
    http_client: httpx.AsyncClient,
) -> bool:
    """Attempt to deliver a webhook. Returns True if successful."""
    payload_str = event.payload
    signature, timestamp = sign_payload(payload_str, subscription.secret)

    body = {
        "event_id": str(event.id),
        "event_type": event.event_type.value,
        "timestamp": timestamp,
        "data": json.loads(payload_str),
    }
    body_str = json.dumps(body)

    attempt_count = await get_attempt_count(session, event.id, subscription.id)
    attempt_number = attempt_count + 1

    attempt = DeliveryAttempt(
        event_id=event.id,
        subscription_id=subscription.id,
        attempt_number=attempt_number,
        status=DeliveryStatus.pending,
    )

    try:
        response = await http_client.post(
            subscription.url,
            content=body_str,
            headers={
                "Content-Type": "application/json",
                "X-Webhook-Signature": signature,
                "X-Webhook-Timestamp": str(timestamp),
                "X-Webhook-Event": event.event_type.value,
                "X-Webhook-Event-Id": str(event.id),
            },
            timeout=settings.delivery_timeout_seconds,
        )

        attempt.http_status_code = response.status_code
        attempt.response_body = response.text[:1000]

        if 200 <= response.status_code < 300:
            attempt.status = DeliveryStatus.delivered
            session.add(attempt)
            await session.commit()
            logger.info(
                "webhook_delivered",
                event_id=str(event.id),
                subscription_id=str(subscription.id),
                status_code=response.status_code,
            )
            return True
        else:
            attempt.status = DeliveryStatus.failed
            attempt.error_message = f"HTTP {response.status_code}"

    except httpx.TimeoutException:
        attempt.status = DeliveryStatus.failed
        attempt.error_message = "Request timed out"
    except httpx.RequestError as exc:
        attempt.status = DeliveryStatus.failed
        attempt.error_message = str(exc)[:500]

    # Schedule retry or dead-letter
    if attempt_number >= settings.delivery_max_attempts:
        attempt.status = DeliveryStatus.dead_lettered
        dead_letter = DeadLetter(
            event_id=event.id,
            subscription_id=subscription.id,
            last_error=attempt.error_message,
            total_attempts=attempt_number,
        )
        session.add(dead_letter)
        logger.warning(
            "event_dead_lettered",
            event_id=str(event.id),
            subscription_id=str(subscription.id),
        )
    else:
        backoff = settings.delivery_backoff_base_seconds ** attempt_number
        attempt.next_retry_at = datetime.now(timezone.utc) + timedelta(seconds=backoff)
        logger.info(
            "webhook_delivery_failed_will_retry",
            event_id=str(event.id),
            subscription_id=str(subscription.id),
            attempt=attempt_number,
            next_retry_seconds=backoff,
        )

    session.add(attempt)
    await session.commit()
    return False


async def process_outbox(http_client: httpx.AsyncClient, session: AsyncSession) -> int:
    """Process pending outbox events. Returns count of deliveries attempted."""
    events = await get_pending_deliveries(session)
    count = 0

    for event in events:
        subscriptions = await get_matching_subscriptions(session, event.event_type.value)
        for sub in subscriptions:
            # Idempotency: skip if already delivered
            if await has_been_delivered(session, event.id, sub.id):
                continue

            # Skip if dead-lettered
            if await is_dead_lettered(session, event.id, sub.id):
                continue

            # Check if there's a pending retry that's not yet due
            attempt_count = await get_attempt_count(session, event.id, sub.id)
            if attempt_count > 0:
                retry = await get_retry_attempt(session, event.id, sub.id)
                if retry is None:
                    # Retry exists but not yet due
                    continue

            await deliver_webhook(session, event, sub, http_client)
            count += 1

    return count


async def replay_dead_letter(
    session: AsyncSession, dead_letter_id: uuid.UUID, http_client: httpx.AsyncClient
) -> bool:
    """Replay a dead-lettered event."""
    dl = await session.get(DeadLetter, dead_letter_id)
    if dl is None:
        return False

    event = await session.get(OutboxEvent, dl.event_id)
    sub = await session.get(WebhookSubscription, dl.subscription_id)
    if event is None or sub is None:
        return False

    # Remove the dead letter entry so it can be redelivered
    await session.delete(dl)

    # Reset any dead_lettered attempts to allow new attempts
    result = await session.execute(
        select(DeliveryAttempt).where(
            and_(
                DeliveryAttempt.event_id == dl.event_id,
                DeliveryAttempt.subscription_id == dl.subscription_id,
                DeliveryAttempt.status == DeliveryStatus.dead_lettered,
            )
        )
    )
    for attempt in result.scalars().all():
        attempt.status = DeliveryStatus.failed

    await session.commit()

    success = await deliver_webhook(session, event, sub, http_client)
    logger.info(
        "dead_letter_replayed",
        dead_letter_id=str(dead_letter_id),
        success=success,
    )
    return success


async def get_delivery_attempts(
    session: AsyncSession, event_id: uuid.UUID
) -> list[DeliveryAttempt]:
    """Get all delivery attempts for an event."""
    result = await session.execute(
        select(DeliveryAttempt)
        .where(DeliveryAttempt.event_id == event_id)
        .order_by(DeliveryAttempt.created_at.asc())
    )
    return list(result.scalars().all())
