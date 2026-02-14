import asyncio

import httpx
import structlog

from integrations_hub.config import settings
from integrations_hub.database import async_session_factory
from integrations_hub.services.delivery import process_outbox

logger = structlog.get_logger()


async def run_delivery_loop() -> None:
    """Background loop that polls the outbox and delivers webhooks."""
    logger.info("delivery_worker_started")
    async with httpx.AsyncClient() as client:
        while True:
            try:
                async with async_session_factory() as session:
                    count = await process_outbox(client, session)
                    if count > 0:
                        logger.info("delivery_cycle_complete", deliveries_attempted=count)
            except Exception:
                logger.exception("delivery_worker_error")
            await asyncio.sleep(settings.delivery_poll_interval_seconds)
