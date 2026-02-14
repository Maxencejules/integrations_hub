import uuid
from datetime import datetime

from pydantic import BaseModel, field_validator

from integrations_hub.models.tables import EventType

VALID_EVENTS = {e.value for e in EventType}


class EventCreate(BaseModel):
    event_type: str
    payload: dict

    @field_validator("event_type")
    @classmethod
    def validate_event_type(cls, v: str) -> str:
        if v not in VALID_EVENTS:
            raise ValueError(f"Invalid event type: {v}. Must be one of {VALID_EVENTS}")
        return v


class EventResponse(BaseModel):
    id: uuid.UUID
    event_type: str
    payload: str
    created_at: datetime

    model_config = {"from_attributes": True}


class DeliveryAttemptResponse(BaseModel):
    id: uuid.UUID
    event_id: uuid.UUID
    subscription_id: uuid.UUID
    attempt_number: int
    status: str
    http_status_code: int | None
    response_body: str | None
    error_message: str | None
    next_retry_at: datetime | None
    created_at: datetime

    model_config = {"from_attributes": True}


class DeadLetterResponse(BaseModel):
    id: uuid.UUID
    event_id: uuid.UUID
    subscription_id: uuid.UUID
    last_error: str | None
    total_attempts: int
    created_at: datetime

    model_config = {"from_attributes": True}
