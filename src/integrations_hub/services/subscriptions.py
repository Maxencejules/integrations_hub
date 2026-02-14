import uuid

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from integrations_hub.models.tables import WebhookSubscription
from integrations_hub.schemas.subscriptions import SubscriptionCreate, SubscriptionUpdate

logger = structlog.get_logger()


async def create_subscription(
    session: AsyncSession, data: SubscriptionCreate
) -> WebhookSubscription:
    sub = WebhookSubscription(
        url=str(data.url),
        secret=data.secret,
        events=",".join(data.events),
        enabled=data.enabled,
    )
    session.add(sub)
    await session.commit()
    await session.refresh(sub)
    logger.info("subscription_created", subscription_id=str(sub.id))
    return sub


async def get_subscription(
    session: AsyncSession, subscription_id: uuid.UUID
) -> WebhookSubscription | None:
    return await session.get(WebhookSubscription, subscription_id)


async def list_subscriptions(session: AsyncSession) -> list[WebhookSubscription]:
    result = await session.execute(
        select(WebhookSubscription).order_by(WebhookSubscription.created_at.desc())
    )
    return list(result.scalars().all())


async def update_subscription(
    session: AsyncSession, subscription_id: uuid.UUID, data: SubscriptionUpdate
) -> WebhookSubscription | None:
    sub = await session.get(WebhookSubscription, subscription_id)
    if sub is None:
        return None
    update_data = data.model_dump(exclude_unset=True)
    if "events" in update_data and update_data["events"] is not None:
        update_data["events"] = ",".join(update_data["events"])
    if "url" in update_data and update_data["url"] is not None:
        update_data["url"] = str(update_data["url"])
    for key, value in update_data.items():
        setattr(sub, key, value)
    await session.commit()
    await session.refresh(sub)
    logger.info("subscription_updated", subscription_id=str(sub.id))
    return sub


async def delete_subscription(
    session: AsyncSession, subscription_id: uuid.UUID
) -> bool:
    sub = await session.get(WebhookSubscription, subscription_id)
    if sub is None:
        return False
    await session.delete(sub)
    await session.commit()
    logger.info("subscription_deleted", subscription_id=str(subscription_id))
    return True
