import uuid

import httpx
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from integrations_hub.database import get_session
from integrations_hub.schemas.events import DeadLetterResponse, DeliveryAttemptResponse
from integrations_hub.services.delivery import (
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
        success = await replay_dead_letter(session, dead_letter_id, client)
    if not success:
        raise HTTPException(status_code=404, detail="Dead letter not found or replay failed")
    return {"status": "replayed", "dead_letter_id": str(dead_letter_id)}
