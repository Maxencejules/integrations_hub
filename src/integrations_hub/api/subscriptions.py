import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from integrations_hub.database import get_session
from integrations_hub.schemas.subscriptions import (
    SubscriptionCreate,
    SubscriptionResponse,
    SubscriptionUpdate,
)
from integrations_hub.services.subscriptions import (
    create_subscription,
    delete_subscription,
    get_subscription,
    list_subscriptions,
    update_subscription,
)

router = APIRouter(prefix="/subscriptions", tags=["Subscriptions"])


@router.post("", response_model=SubscriptionResponse, status_code=201)
async def create(data: SubscriptionCreate, session: AsyncSession = Depends(get_session)):
    sub = await create_subscription(session, data)
    return sub


@router.get("", response_model=list[SubscriptionResponse])
async def list_all(session: AsyncSession = Depends(get_session)):
    return await list_subscriptions(session)


@router.get("/{subscription_id}", response_model=SubscriptionResponse)
async def get_one(subscription_id: uuid.UUID, session: AsyncSession = Depends(get_session)):
    sub = await get_subscription(session, subscription_id)
    if sub is None:
        raise HTTPException(status_code=404, detail="Subscription not found")
    return sub


@router.put("/{subscription_id}", response_model=SubscriptionResponse)
async def update(
    subscription_id: uuid.UUID,
    data: SubscriptionUpdate,
    session: AsyncSession = Depends(get_session),
):
    sub = await update_subscription(session, subscription_id, data)
    if sub is None:
        raise HTTPException(status_code=404, detail="Subscription not found")
    return sub


@router.delete("/{subscription_id}", status_code=204)
async def delete(subscription_id: uuid.UUID, session: AsyncSession = Depends(get_session)):
    deleted = await delete_subscription(session, subscription_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Subscription not found")
