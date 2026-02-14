import os
from collections.abc import AsyncGenerator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import (
    AsyncConnection,
    AsyncSession,
    AsyncTransaction,
    async_sessionmaker,
    create_async_engine,
)

from integrations_hub.database import get_session
from integrations_hub.models import Base
from integrations_hub.main import app

TEST_DATABASE_URL = os.environ.get(
    "IH_DATABASE_URL",
    "postgresql+asyncpg://postgres:postgres@localhost:5432/integrations_hub_test",
)

engine = create_async_engine(TEST_DATABASE_URL, echo=False)


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def _setup_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest_asyncio.fixture(loop_scope="session")
async def db_session(_setup_db) -> AsyncGenerator[AsyncSession, None]:
    """Each test gets a session bound to a transaction that is rolled back after the test."""
    conn: AsyncConnection = await engine.connect()
    txn: AsyncTransaction = await conn.begin()

    session = AsyncSession(bind=conn, expire_on_commit=False)

    # Override commit to use flush so data is visible but the outer txn isn't committed
    _original_commit = session.commit

    async def _flush_instead_of_commit():
        await session.flush()

    session.commit = _flush_instead_of_commit  # type: ignore[assignment]

    yield session

    await session.close()
    await txn.rollback()
    await conn.close()


@pytest_asyncio.fixture(loop_scope="session")
async def client(db_session: AsyncSession) -> AsyncGenerator[AsyncClient, None]:
    async def override_session():
        yield db_session

    app.dependency_overrides[get_session] = override_session
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()
