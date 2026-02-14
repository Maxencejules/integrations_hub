"""Initial schema

Revision ID: 001
Revises:
Create Date: 2024-01-01 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Event type enum
    event_type_enum = postgresql.ENUM(
        "request_submitted",
        "request_approved",
        "request_rejected",
        "request_updated",
        name="event_type_enum",
        create_type=False,
    )
    event_type_enum.create(op.get_bind(), checkfirst=True)

    # Delivery status enum
    delivery_status_enum = postgresql.ENUM(
        "pending",
        "delivered",
        "failed",
        "dead_lettered",
        name="delivery_status_enum",
        create_type=False,
    )
    delivery_status_enum.create(op.get_bind(), checkfirst=True)

    # Webhook subscriptions
    op.create_table(
        "webhook_subscriptions",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("url", sa.String(2048), nullable=False),
        sa.Column("secret", sa.String(256), nullable=False),
        sa.Column("enabled", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("events", sa.Text, nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
        ),
    )

    # Outbox events
    op.create_table(
        "outbox_events",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "event_type",
            event_type_enum,
            nullable=False,
        ),
        sa.Column("payload", sa.Text, nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_outbox_events_created_at", "outbox_events", ["created_at"])

    # Delivery attempts
    op.create_table(
        "delivery_attempts",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "event_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("outbox_events.id"),
            nullable=False,
        ),
        sa.Column(
            "subscription_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("webhook_subscriptions.id"),
            nullable=False,
        ),
        sa.Column("attempt_number", sa.Integer, nullable=False, server_default="1"),
        sa.Column(
            "status",
            delivery_status_enum,
            nullable=False,
            server_default="pending",
        ),
        sa.Column("http_status_code", sa.Integer, nullable=True),
        sa.Column("response_body", sa.Text, nullable=True),
        sa.Column("error_message", sa.Text, nullable=True),
        sa.Column("next_retry_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint(
            "event_id",
            "subscription_id",
            "attempt_number",
            name="uq_delivery_idempotency",
        ),
    )
    op.create_index(
        "ix_delivery_attempts_pending",
        "delivery_attempts",
        ["status", "next_retry_at"],
    )

    # Dead letters
    op.create_table(
        "dead_letters",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "event_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("outbox_events.id"),
            nullable=False,
        ),
        sa.Column(
            "subscription_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("webhook_subscriptions.id"),
            nullable=False,
        ),
        sa.Column("last_error", sa.Text, nullable=True),
        sa.Column("total_attempts", sa.Integer, nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint(
            "event_id", "subscription_id", name="uq_dead_letter_event_sub"
        ),
    )


def downgrade() -> None:
    op.drop_table("dead_letters")
    op.drop_table("delivery_attempts")
    op.drop_table("outbox_events")
    op.drop_table("webhook_subscriptions")
    op.execute("DROP TYPE IF EXISTS delivery_status_enum")
    op.execute("DROP TYPE IF EXISTS event_type_enum")
