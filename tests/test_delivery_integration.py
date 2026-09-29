"""Delivery state-machine regressions, including real PostgreSQL row locking."""

import asyncio
import hashlib
import hmac
import json
import os
import uuid
from datetime import datetime, timedelta, timezone

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from integrations_hub.models.tables import (
    DeadLetter,
    DeliveryAttempt,
    DeliveryStatus,
    EventType,
    OutboxEvent,
    WebhookSubscription,
)
from integrations_hub.services import delivery

pytestmark = pytest.mark.asyncio


@pytest.fixture
def retry_settings(monkeypatch):
    monkeypatch.setattr(delivery.settings, "delivery_max_attempts", 3)
    monkeypatch.setattr(delivery.settings, "delivery_backoff_base_seconds", 2.0)


def make_event(*, created_at=None, event_type=EventType.request_submitted):
    return OutboxEvent(
        id=uuid.uuid4(),
        event_type=event_type,
        payload=json.dumps({"title": "Delivery regression"}),
        created_at=created_at or datetime.now(timezone.utc),
    )


def make_subscription():
    return WebhookSubscription(
        id=uuid.uuid4(),
        url="https://example.com/webhook",
        secret="integration-regression-secret",
        enabled=True,
        events=EventType.request_submitted.value,
    )


def make_attempt(event, subscription, number, status, *, due=None):
    return DeliveryAttempt(
        id=uuid.uuid4(),
        event_id=event.id,
        subscription_id=subscription.id,
        attempt_number=number,
        status=status,
        next_retry_at=due,
    )


async def attempts_for(session, event_id, subscription_id):
    result = await session.execute(
        select(DeliveryAttempt)
        .where(
            DeliveryAttempt.event_id == event_id,
            DeliveryAttempt.subscription_id == subscription_id,
        )
        .order_by(DeliveryAttempt.attempt_number)
    )
    return list(result.scalars().all())


async def dead_letters_for(session, event_id, subscription_id):
    result = await session.execute(
        select(DeadLetter).where(
            DeadLetter.event_id == event_id,
            DeadLetter.subscription_id == subscription_id,
        )
    )
    return list(result.scalars().all())


async def test_failed_deliveries_retry_only_when_the_latest_attempt_is_due(
    db_session, retry_settings
):
    event, subscription = make_event(), make_subscription()
    db_session.add_all([event, subscription])
    await db_session.flush()
    requests = []
    statuses = iter([500, 500, 200])

    def receive(request):
        requests.append(request)
        return httpx.Response(next(statuses), text="response")

    async with httpx.AsyncClient(transport=httpx.MockTransport(receive)) as client:
        assert await delivery.process_outbox(client, db_session) == 1
        attempts = await attempts_for(db_session, event.id, subscription.id)
        assert attempts[0].status == DeliveryStatus.failed
        assert attempts[0].next_retry_at > datetime.now(timezone.utc)
        assert await delivery.process_outbox(client, db_session) == 0

        attempts[0].next_retry_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        await db_session.flush()
        assert await delivery.process_outbox(client, db_session) == 1
        attempts = await attempts_for(db_session, event.id, subscription.id)
        assert [attempt.status for attempt in attempts] == [
            DeliveryStatus.failed,
            DeliveryStatus.failed,
        ]
        # Attempt 1 remains due, but cannot override attempt 2's future retry.
        assert attempts[0].next_retry_at < datetime.now(timezone.utc)
        assert attempts[1].next_retry_at > datetime.now(timezone.utc)
        assert await delivery.process_outbox(client, db_session) == 0

        attempts[1].next_retry_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        await db_session.flush()
        assert await delivery.process_outbox(client, db_session) == 1
        assert await delivery.process_outbox(client, db_session) == 0

    attempts = await attempts_for(db_session, event.id, subscription.id)
    assert [attempt.attempt_number for attempt in attempts] == [1, 2, 3]
    assert [attempt.status for attempt in attempts] == [
        DeliveryStatus.failed,
        DeliveryStatus.failed,
        DeliveryStatus.delivered,
    ]
    assert [attempt.http_status_code for attempt in attempts] == [500, 500, 200]
    assert len(requests) == 3
    assert await dead_letters_for(db_session, event.id, subscription.id) == []


async def test_old_due_retry_does_not_override_a_newer_future_retry(db_session):
    event, subscription = make_event(), make_subscription()
    db_session.add_all([event, subscription])
    await db_session.flush()
    now = datetime.now(timezone.utc)
    # A historical pending row must not supersede the latest failed attempt.
    db_session.add_all(
        [
            make_attempt(
                event, subscription, 1, DeliveryStatus.pending, due=now - timedelta(days=1)
            ),
            make_attempt(
                event, subscription, 2, DeliveryStatus.failed, due=now + timedelta(days=1)
            ),
        ]
    )
    await db_session.flush()
    requests = []

    def receive(request):
        requests.append(request)
        return httpx.Response(200)

    async with httpx.AsyncClient(transport=httpx.MockTransport(receive)) as client:
        assert await delivery.process_outbox(client, db_session) == 0
    assert requests == []
    assert len(await attempts_for(db_session, event.id, subscription.id)) == 2


@pytest.mark.parametrize("blocked_state", ["delivered", "dead_lettered", "unmatched", "future"])
async def test_ineligible_first_fifty_events_do_not_starve_event_fifty_one(
    db_session, blocked_state
):
    subscription = make_subscription()
    start = datetime.now(timezone.utc) - timedelta(days=1)
    event_type = (
        EventType.request_updated if blocked_state == "unmatched" else EventType.request_submitted
    )
    old_events = [
        make_event(created_at=start + timedelta(seconds=index), event_type=event_type)
        for index in range(50)
    ]
    ready_event = make_event(created_at=start + timedelta(seconds=50))
    db_session.add_all([subscription, *old_events, ready_event])
    await db_session.flush()

    for event in old_events:
        if blocked_state == "delivered":
            db_session.add(make_attempt(event, subscription, 1, DeliveryStatus.delivered))
        elif blocked_state == "dead_lettered":
            db_session.add(make_attempt(event, subscription, 1, DeliveryStatus.dead_lettered))
            db_session.add(
                DeadLetter(event_id=event.id, subscription_id=subscription.id, total_attempts=1)
            )
        elif blocked_state == "future":
            db_session.add(
                make_attempt(
                    event,
                    subscription,
                    1,
                    DeliveryStatus.failed,
                    due=datetime.now(timezone.utc) + timedelta(days=1),
                )
            )
    await db_session.flush()
    sent_ids = []

    def receive(request):
        sent_ids.append(json.loads(request.content)["event_id"])
        return httpx.Response(200)

    async with httpx.AsyncClient(transport=httpx.MockTransport(receive)) as client:
        assert await delivery.process_outbox(client, db_session) == 1
        assert await delivery.process_outbox(client, db_session) == 0
    assert sent_ids == [str(ready_event.id)]


async def test_replay_keeps_history_and_resets_cycle_budget_with_lifetime_numbers(
    db_session, monkeypatch
):
    monkeypatch.setattr(delivery.settings, "delivery_max_attempts", 2)
    monkeypatch.setattr(delivery.settings, "delivery_backoff_base_seconds", 2.0)
    started = datetime.now(timezone.utc)

    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return started if tz is not None else started.replace(tzinfo=None)

    monkeypatch.setattr(delivery, "datetime", FrozenDateTime)
    event, subscription = make_event(), make_subscription()
    db_session.add_all([event, subscription])
    await db_session.flush()
    old_failure = make_attempt(event, subscription, 1, DeliveryStatus.failed)
    old_terminal = make_attempt(event, subscription, 2, DeliveryStatus.dead_lettered)
    dead_letter = DeadLetter(
        id=uuid.uuid4(), event_id=event.id, subscription_id=subscription.id, total_attempts=2
    )
    db_session.add_all([old_failure, old_terminal, dead_letter])
    await db_session.flush()
    requests = []
    statuses = iter([500, 500, 200])

    def receive(request):
        requests.append(request)
        return httpx.Response(next(statuses))

    async with httpx.AsyncClient(transport=httpx.MockTransport(receive)) as client:
        assert await delivery.replay_dead_letter(db_session, dead_letter.id, client) is False
        attempts = await attempts_for(db_session, event.id, subscription.id)
        assert [attempt.status for attempt in attempts] == [
            DeliveryStatus.failed,
            DeliveryStatus.dead_lettered,
            DeliveryStatus.failed,
        ]
        # Replay attempt 3 is the first in its new cycle, with the first backoff.
        assert attempts[2].next_retry_at == started + timedelta(seconds=2)
        assert await dead_letters_for(db_session, event.id, subscription.id) == []
        assert await delivery.process_outbox(client, db_session) == 0

        attempts[2].next_retry_at = started - timedelta(seconds=1)
        await db_session.flush()
        assert await delivery.process_outbox(client, db_session) == 1
        letters = await dead_letters_for(db_session, event.id, subscription.id)
        assert len(letters) == 1
        assert letters[0].total_attempts == 4
        assert await delivery.process_outbox(client, db_session) == 0
        assert await delivery.replay_dead_letter(db_session, letters[0].id, client) is True

    attempts = await attempts_for(db_session, event.id, subscription.id)
    assert [attempt.attempt_number for attempt in attempts] == [1, 2, 3, 4, 5]
    assert [attempt.status for attempt in attempts] == [
        DeliveryStatus.failed,
        DeliveryStatus.dead_lettered,
        DeliveryStatus.failed,
        DeliveryStatus.dead_lettered,
        DeliveryStatus.delivered,
    ]
    assert await dead_letters_for(db_session, event.id, subscription.id) == []
    assert len(requests) == 3


async def test_replay_returns_none_for_a_missing_dead_letter(db_session):
    def receive(request):
        pytest.fail("a missing dead letter must not trigger an HTTP request")

    async with httpx.AsyncClient(transport=httpx.MockTransport(receive)) as client:
        assert await delivery.replay_dead_letter(db_session, uuid.uuid4(), client) is None


@pytest_asyncio.fixture(loop_scope="session")
async def committed_pair(_setup_db):
    """Committed rows and independent connections are essential for lock tests.

    The shared db_session fixture intentionally replaces commits with flushes,
    so a second connection cannot observe its uncommitted seeds.
    """
    database_url = os.environ.get(
        "IH_DATABASE_URL",
        "postgresql+asyncpg://postgres:postgres@localhost:5432/integrations_hub_test",
    )
    engine = create_async_engine(database_url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    event, subscription = make_event(), make_subscription()
    async with sessions.begin() as session:
        session.add_all([event, subscription])
    try:
        yield sessions, event, subscription
    finally:
        async with sessions.begin() as session:
            await session.execute(delete(DeadLetter).where(DeadLetter.event_id == event.id))
            await session.execute(
                delete(DeliveryAttempt).where(DeliveryAttempt.event_id == event.id)
            )
            await session.execute(delete(OutboxEvent).where(OutboxEvent.id == event.id))
            await session.execute(
                delete(WebhookSubscription).where(WebhookSubscription.id == subscription.id)
            )
        await engine.dispose()


@pytest.mark.parametrize(
    ("first_operation", "second_operation"),
    [("worker", "worker"), ("replay", "worker"), ("replay", "replay")],
)
async def test_concurrent_operations_cannot_send_the_same_pair(
    committed_pair, first_operation, second_operation
):
    sessions, event, subscription = committed_pair
    dead_letter_id = uuid.uuid4()
    if first_operation == "replay":
        async with sessions.begin() as session:
            # Include a historical due pending row to expose a worker entering
            # during replay if replay releases its transaction before HTTP.
            session.add_all(
                [
                    make_attempt(
                        event,
                        subscription,
                        1,
                        DeliveryStatus.pending,
                        due=datetime.now(timezone.utc) - timedelta(days=1),
                    ),
                    make_attempt(event, subscription, 2, DeliveryStatus.dead_lettered),
                    DeadLetter(
                        id=dead_letter_id,
                        event_id=event.id,
                        subscription_id=subscription.id,
                        total_attempts=2,
                    ),
                ]
            )

    entered, release, duplicate = asyncio.Event(), asyncio.Event(), asyncio.Event()
    requests = []

    async def receive(request):
        assert json.loads(request.content)["event_id"] == str(event.id)
        requests.append(request)
        if len(requests) > 1:
            duplicate.set()
        entered.set()
        await release.wait()
        return httpx.Response(200)

    async with httpx.AsyncClient(transport=httpx.MockTransport(receive)) as client:

        async def first_operation_call():
            async with sessions() as session:
                if first_operation == "replay":
                    return await delivery.replay_dead_letter(session, dead_letter_id, client)
                return await delivery.process_outbox(client, session)

        async def contender_call():
            async with sessions() as session:
                if second_operation == "replay":
                    with pytest.raises(delivery.DeliveryInProgressError):
                        await delivery.replay_dead_letter(session, dead_letter_id, client)
                    return "busy"
                return await delivery.process_outbox(client, session)

        first = asyncio.create_task(first_operation_call())
        contender = duplicate_watch = None
        try:
            await asyncio.wait_for(entered.wait(), timeout=5)
            contender = asyncio.create_task(contender_call())
            duplicate_watch = asyncio.create_task(duplicate.wait())
            done, _ = await asyncio.wait(
                {contender, duplicate_watch},
                timeout=5,
                return_when=asyncio.FIRST_COMPLETED,
            )
            assert done, "the second worker neither skipped the claimed event nor sent a request"
            assert not duplicate.is_set(), (
                "two HTTP sends overlapped for the same event/subscription"
            )
            assert contender in done, "a worker should skip an event claimed by another transaction"
            assert contender.result() == ("busy" if second_operation == "replay" else 0)
        finally:
            release.set()
            if duplicate_watch is not None:
                duplicate_watch.cancel()
                await asyncio.gather(duplicate_watch, return_exceptions=True)
            tasks = [first] + ([contender] if contender is not None else [])
            results = await asyncio.wait_for(
                asyncio.gather(*tasks, return_exceptions=True), timeout=5
            )

    assert results[0] == (True if first_operation == "replay" else 1)
    assert len(requests) == 1
    async with sessions() as session:
        attempts = await attempts_for(session, event.id, subscription.id)
        assert sum(attempt.status == DeliveryStatus.delivered for attempt in attempts) == 1
        if first_operation == "replay":
            assert [attempt.attempt_number for attempt in attempts] == [1, 2, 3]
            assert attempts[1].status == DeliveryStatus.dead_lettered


async def test_worker_delivers_an_unrelated_event_while_another_event_is_claimed(committed_pair):
    sessions, claimed_event, subscription = committed_pair
    unrelated_event = make_event()
    entered, release = asyncio.Event(), asyncio.Event()
    sent_ids = []

    async def receive(request):
        event_id = json.loads(request.content)["event_id"]
        sent_ids.append(event_id)
        if event_id == str(claimed_event.id):
            assert sent_ids.count(event_id) == 1, "a second worker sent the claimed event"
            entered.set()
            await release.wait()
        else:
            assert event_id == str(unrelated_event.id)
        return httpx.Response(200)

    async def worker_call(client):
        async with sessions() as session:
            return await delivery.process_outbox(client, session)

    try:
        async with httpx.AsyncClient(transport=httpx.MockTransport(receive)) as client:
            first = asyncio.create_task(worker_call(client))
            second = None
            try:
                await asyncio.wait_for(entered.wait(), timeout=5)
                # Seed only after the first worker has claimed its original event.
                async with sessions.begin() as session:
                    session.add(unrelated_event)
                second = asyncio.create_task(worker_call(client))
                assert await asyncio.wait_for(asyncio.shield(second), timeout=5) == 1
                assert not first.done()
                assert sent_ids == [str(claimed_event.id), str(unrelated_event.id)]
            finally:
                release.set()
                tasks = [first] + ([second] if second is not None else [])
                results = await asyncio.wait_for(
                    asyncio.gather(*tasks, return_exceptions=True), timeout=5
                )
        assert results == [1, 1]
        async with sessions() as session:
            for event in (claimed_event, unrelated_event):
                attempts = await attempts_for(session, event.id, subscription.id)
                assert len(attempts) == 1
                assert attempts[0].status == DeliveryStatus.delivered
    finally:
        async with sessions.begin() as session:
            await session.execute(
                delete(DeliveryAttempt).where(DeliveryAttempt.event_id == unrelated_event.id)
            )
            await session.execute(delete(OutboxEvent).where(OutboxEvent.id == unrelated_event.id))


@pytest.mark.parametrize("subscription_change", ["deleted", "rotated"])
async def test_worker_rechecks_preselected_subscription_before_sending(
    committed_pair, monkeypatch, subscription_change
):
    sessions, event, subscription = committed_pair
    matching_subscriptions = delivery.get_matching_subscriptions
    new_url = "https://new.example.com/webhook"
    new_secret = "rotated-integration-regression-secret"

    async def change_after_selection(session, event_type):
        selected = await matching_subscriptions(session, event_type)
        assert [sub.id for sub in selected] == [subscription.id]
        # The worker already holds an ORM object with the original credentials.
        # Apply a committed administrative change from another connection.
        async with sessions.begin() as writer:
            if subscription_change == "deleted":
                await writer.execute(
                    delete(WebhookSubscription).where(WebhookSubscription.id == subscription.id)
                )
            else:
                await writer.execute(
                    update(WebhookSubscription)
                    .where(WebhookSubscription.id == subscription.id)
                    .values(url=new_url, secret=new_secret)
                )
        return selected

    monkeypatch.setattr(delivery, "get_matching_subscriptions", change_after_selection)
    requests = []

    def receive(request):
        requests.append(request)
        return httpx.Response(200)

    async with httpx.AsyncClient(transport=httpx.MockTransport(receive)) as client:
        async with sessions() as session:
            result = await delivery.process_outbox(client, session)

    async with sessions() as session:
        attempts = await attempts_for(session, event.id, subscription.id)
    if subscription_change == "deleted":
        assert result == 0
        assert requests == []
        assert attempts == []
    else:
        assert result == 1
        assert len(requests) == len(attempts) == 1
        request = requests[0]
        assert str(request.url) == new_url
        message = request.headers["X-Webhook-Timestamp"].encode() + b"." + request.content
        expected = hmac.new(new_secret.encode(), message, hashlib.sha256).hexdigest()
        assert hmac.compare_digest(request.headers["X-Webhook-Signature"], expected)
