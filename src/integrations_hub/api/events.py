from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from integrations_hub.connectors.slack import send_slack_notification
from integrations_hub.database import get_session
from integrations_hub.schemas.events import EventCreate, EventResponse
from integrations_hub.services.outbox import publish_event

router = APIRouter(prefix="/events", tags=["Events"])


@router.post("", response_model=EventResponse, status_code=201)
async def create_event(data: EventCreate, session: AsyncSession = Depends(get_session)):
    event = await publish_event(session, data.event_type, data.payload)

    # Fire Slack connector for request_submitted
    if data.event_type == "request_submitted":
        await send_slack_notification(event)

    return event
