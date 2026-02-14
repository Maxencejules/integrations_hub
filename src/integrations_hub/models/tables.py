import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from integrations_hub.models.base import Base


class EventType(str, enum.Enum):
    request_submitted = "request_submitted"
    request_approved = "request_approved"
    request_rejected = "request_rejected"
    request_updated = "request_updated"


class DeliveryStatus(str, enum.Enum):
    pending = "pending"
    delivered = "delivered"
    failed = "failed"
    dead_lettered = "dead_lettered"


class WebhookSubscription(Base):
    __tablename__ = "webhook_subscriptions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    url: Mapped[str] = mapped_column(String(2048), nullable=False)
    secret: Mapped[str] = mapped_column(String(256), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    events: Mapped[str] = mapped_column(
        Text, nullable=False
    )  # comma-separated EventType values
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class OutboxEvent(Base):
    __tablename__ = "outbox_events"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    event_type: Mapped[EventType] = mapped_column(
        Enum(EventType, name="event_type_enum"), nullable=False
    )
    payload: Mapped[str] = mapped_column(Text, nullable=False)  # JSON string
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    delivery_attempts: Mapped[list["DeliveryAttempt"]] = relationship(
        back_populates="event", cascade="all, delete-orphan"
    )

    __table_args__ = (Index("ix_outbox_events_created_at", "created_at"),)


class DeliveryAttempt(Base):
    __tablename__ = "delivery_attempts"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    event_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("outbox_events.id"), nullable=False
    )
    subscription_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("webhook_subscriptions.id"), nullable=False
    )
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    status: Mapped[DeliveryStatus] = mapped_column(
        Enum(DeliveryStatus, name="delivery_status_enum"),
        nullable=False,
        default=DeliveryStatus.pending,
    )
    http_status_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    response_body: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    next_retry_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    event: Mapped["OutboxEvent"] = relationship(back_populates="delivery_attempts")

    __table_args__ = (
        UniqueConstraint("event_id", "subscription_id", "attempt_number", name="uq_delivery_idempotency"),
        Index("ix_delivery_attempts_pending", "status", "next_retry_at"),
    )


class DeadLetter(Base):
    __tablename__ = "dead_letters"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    event_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("outbox_events.id"), nullable=False
    )
    subscription_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("webhook_subscriptions.id"), nullable=False
    )
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    total_attempts: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    __table_args__ = (
        UniqueConstraint("event_id", "subscription_id", name="uq_dead_letter_event_sub"),
    )
