import json
import time
import uuid
from datetime import datetime, timedelta, timezone

import httpx
import structlog
from sqlalchemy import String, case, cast, func, or_, select
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


class DeliveryInProgressError(Exception):
    """Another transaction currently owns this event's delivery."""


async def get_pending_deliveries(session: AsyncSession) -> list[OutboxEvent]:
    """Limit eligible work, rather than the first fifty historical events."""
    pair = (
        DeliveryAttempt.event_id == OutboxEvent.id,
        DeliveryAttempt.subscription_id == WebhookSubscription.id,
    )
    latest_number = (
        select(func.max(DeliveryAttempt.attempt_number))
        .where(*pair)
        .correlate(OutboxEvent, WebhookSubscription)
        .scalar_subquery()
    )
    cycle_boundary = (
        select(func.coalesce(func.max(DeliveryAttempt.attempt_number), 0))
        .where(*pair, DeliveryAttempt.status == DeliveryStatus.dead_lettered)
        .correlate(OutboxEvent, WebhookSubscription)
        .scalar_subquery()
    )
    delivered = (
        select(DeliveryAttempt.id)
        .where(*pair, DeliveryAttempt.status == DeliveryStatus.delivered)
        .correlate(OutboxEvent, WebhookSubscription)
        .exists()
    )
    dead_lettered = (
        select(DeadLetter.id)
        .where(
            DeadLetter.event_id == OutboxEvent.id,
            DeadLetter.subscription_id == WebhookSubscription.id,
        )
        .correlate(OutboxEvent, WebhookSubscription)
        .exists()
    )
    due_retry = (
        select(DeliveryAttempt.id)
        .where(
            *pair,
            DeliveryAttempt.attempt_number == latest_number,
            DeliveryAttempt.status == DeliveryStatus.failed,
            DeliveryAttempt.next_retry_at <= datetime.now(timezone.utc),
            DeliveryAttempt.attempt_number - cycle_boundary < settings.delivery_max_attempts,
        )
        .correlate(OutboxEvent, WebhookSubscription)
        .exists()
    )
    ready_subscription = (
        select(WebhookSubscription.id)
        .where(
            WebhookSubscription.enabled.is_(True),
            WebhookSubscription.events.contains(cast(OutboxEvent.event_type, String)),
            ~delivered,
            ~dead_lettered,
            or_(latest_number.is_(None), due_retry),
        )
        .correlate(OutboxEvent)
        .exists()
    )
    result = await session.execute(
        select(OutboxEvent)
        .where(ready_subscription)
        .order_by(OutboxEvent.created_at.asc(), OutboxEvent.id)
        .limit(50)
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
    return result.first() is not None


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
    """Only the latest attempt can make a pair ready for an automatic retry."""
    result = await session.execute(
        select(DeliveryAttempt)
        .where(
            DeliveryAttempt.event_id == event_id,
            DeliveryAttempt.subscription_id == subscription_id,
        )
        .order_by(DeliveryAttempt.attempt_number.desc())
        .limit(1)
    )
    latest = result.scalar_one_or_none()
    if (
        latest is None
        or latest.status != DeliveryStatus.failed
        or latest.next_retry_at is None
        or latest.next_retry_at > datetime.now(timezone.utc)
    ):
        return None
    _, boundary = await _get_attempt_numbers(session, event_id, subscription_id)
    if latest.attempt_number - boundary >= settings.delivery_max_attempts:
        return None
    return latest


async def _get_attempt_numbers(
    session: AsyncSession, event_id: uuid.UUID, subscription_id: uuid.UUID
) -> tuple[int, int]:
    """Return lifetime sequence and the last immutable retry-cycle boundary."""
    result = await session.execute(
        select(
            func.coalesce(func.max(DeliveryAttempt.attempt_number), 0),
            func.coalesce(
                func.max(
                    case(
                        (
                            DeliveryAttempt.status == DeliveryStatus.dead_lettered,
                            DeliveryAttempt.attempt_number,
                        ),
                        else_=0,
                    )
                ),
                0,
            ),
        ).where(
            DeliveryAttempt.event_id == event_id,
            DeliveryAttempt.subscription_id == subscription_id,
        )
    )
    last_number, boundary = result.one()
    return last_number, boundary


async def _claim_event(session: AsyncSession, event_id: uuid.UUID) -> bool:
    """Serialize worker/replay sends until their HTTP result is committed."""
    result = await session.execute(
        select(OutboxEvent.id).where(OutboxEvent.id == event_id).with_for_update(skip_locked=True)
    )
    return result.scalar_one_or_none() is not None


async def deliver_webhook(
    session: AsyncSession,
    event: OutboxEvent,
    subscription: WebhookSubscription,
    http_client: httpx.AsyncClient,
) -> bool:
    """Send and persist one attempt; worker/replay callers hold the event lock."""
    payload_str = event.payload
    timestamp = int(time.time())

    body = {
        "event_id": str(event.id),
        "event_type": event.event_type.value,
        "timestamp": timestamp,
        "data": json.loads(payload_str),
    }
    body_str = json.dumps(body)
    signature, _ = sign_payload(body_str, subscription.secret, timestamp)

    last_number, cycle_boundary = await _get_attempt_numbers(session, event.id, subscription.id)
    attempt_number = last_number + 1
    cycle_attempt_number = attempt_number - cycle_boundary

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
    if cycle_attempt_number >= settings.delivery_max_attempts:
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
        backoff = settings.delivery_backoff_base_seconds**cycle_attempt_number
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
            # Claim separately: a commit after one send releases its row lock.
            if not await _claim_event(session, event.id):
                continue
            # Read current credentials/routing and keep them stable through send.
            sub = await session.get(
                WebhookSubscription,
                sub.id,
                populate_existing=True,
                with_for_update={"read": True},
            )
            if (
                sub is None
                or not sub.enabled
                or event.event_type.value not in sub.events.split(",")
            ):
                await session.commit()
                continue
            # Idempotency: skip if already delivered
            if await has_been_delivered(session, event.id, sub.id):
                await session.commit()
                continue

            # Skip if dead-lettered
            if await is_dead_lettered(session, event.id, sub.id):
                await session.commit()
                continue

            last_number, _ = await _get_attempt_numbers(session, event.id, sub.id)
            if last_number > 0:
                retry = await get_retry_attempt(session, event.id, sub.id)
                if retry is None:
                    await session.commit()
                    continue

            await deliver_webhook(session, event, sub, http_client)
            count += 1

    return count


async def replay_dead_letter(
    session: AsyncSession, dead_letter_id: uuid.UUID, http_client: httpx.AsyncClient
) -> bool | None:
    """Start a fresh retry cycle, preserving lifetime history and numbering.

    None means the dead letter/resources no longer exist; False means the
    receiver failed, with the attempt's retry/dead-letter outcome persisted.
    """
    dl = await session.get(DeadLetter, dead_letter_id)
    if dl is None:
        return None

    event = await session.get(OutboxEvent, dl.event_id)
    if event is None:
        return None

    if not await _claim_event(session, event.id):
        raise DeliveryInProgressError("This event is already being delivered")
    # Recheck after claiming: another replay may have consumed the item.
    result = await session.execute(select(DeadLetter).where(DeadLetter.id == dead_letter_id))
    dl = result.scalar_one_or_none()
    if dl is None:
        await session.commit()
        return None
    sub = await session.get(
        WebhookSubscription,
        dl.subscription_id,
        populate_existing=True,
        with_for_update={"read": True},
    )
    if sub is None:
        await session.commit()
        return None
    await session.delete(dl)
    # Flush the removal before a max-attempts=1 replay may insert a new DL.
    # Do not commit here: the claim must remain live across the HTTP request.
    await session.flush()

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
