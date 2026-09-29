import uuid

import httpx
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from integrations_hub.database import get_session
from integrations_hub.schemas.events import DeliveryAttemptResponse
from integrations_hub.services.delivery import (
    DeliveryInProgressError,
    get_delivery_attempts,
    replay_dead_letter,
)

router = APIRouter(prefix="/admin", tags=["Admin"])


@router.get("/events/{event_id}/attempts", response_model=list[DeliveryAttemptResponse])
async def list_attempts(event_id: uuid.UUID, session: AsyncSession = Depends(get_session)):
    attempts = await get_delivery_attempts(session, event_id)
    return attempts


@router.post("/dead-letters/{dead_letter_id}/replay", status_code=200)
async def replay(dead_letter_id: uuid.UUID, session: AsyncSession = Depends(get_session)):
    async with httpx.AsyncClient() as client:
        try:
            success = await replay_dead_letter(session, dead_letter_id, client)
        except DeliveryInProgressError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
    if success is None:
        raise HTTPException(status_code=404, detail="Dead letter not found")
    return {"status": "replayed", "delivered": success, "dead_letter_id": str(dead_letter_id)}
