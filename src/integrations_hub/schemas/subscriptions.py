import uuid
from datetime import datetime

from pydantic import BaseModel, HttpUrl, field_validator

from integrations_hub.models.tables import EventType

VALID_EVENTS = {e.value for e in EventType}


class SubscriptionCreate(BaseModel):
    url: HttpUrl
    secret: str
    events: list[str]
    enabled: bool = True

    @field_validator("events")
    @classmethod
    def validate_events(cls, v: list[str]) -> list[str]:
        if not v:
            raise ValueError("At least one event type is required")
        for event in v:
            if event not in VALID_EVENTS:
                raise ValueError(f"Invalid event type: {event}. Must be one of {VALID_EVENTS}")
        return v

    @field_validator("secret")
    @classmethod
    def validate_secret(cls, v: str) -> str:
        if len(v) < 16:
            raise ValueError("Secret must be at least 16 characters")
        return v


class SubscriptionUpdate(BaseModel):
    url: HttpUrl | None = None
    secret: str | None = None
    events: list[str] | None = None
    enabled: bool | None = None

    @field_validator("events")
    @classmethod
    def validate_events(cls, v: list[str] | None) -> list[str] | None:
        if v is not None:
            if not v:
                raise ValueError("At least one event type is required")
            for event in v:
                if event not in VALID_EVENTS:
                    raise ValueError(
                        f"Invalid event type: {event}. Must be one of {VALID_EVENTS}"
                    )
        return v

    @field_validator("secret")
    @classmethod
    def validate_secret(cls, v: str | None) -> str | None:
        if v is not None and len(v) < 16:
            raise ValueError("Secret must be at least 16 characters")
        return v


class SubscriptionResponse(BaseModel):
    id: uuid.UUID
    url: str
    events: list[str]
    enabled: bool
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}

    @field_validator("events", mode="before")
    @classmethod
    def split_events(cls, v: str | list[str]) -> list[str]:
        if isinstance(v, str):
            return [e.strip() for e in v.split(",") if e.strip()]
        return v
