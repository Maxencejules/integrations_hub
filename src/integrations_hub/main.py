import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI

from integrations_hub.api.admin import router as admin_router
from integrations_hub.api.events import router as events_router
from integrations_hub.api.subscriptions import router as subscriptions_router
from integrations_hub.logging_config import setup_logging
from integrations_hub.metrics import metrics_endpoint
from integrations_hub.worker.delivery_worker import run_delivery_loop

setup_logging()


@asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.create_task(run_delivery_loop())
    yield
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


app = FastAPI(
    title="Integrations Hub",
    description="Outgoing webhook delivery service with connector integrations",
    version="0.1.0",
    lifespan=lifespan,
)

app.include_router(subscriptions_router, prefix="/api/v1")
app.include_router(events_router, prefix="/api/v1")
app.include_router(admin_router, prefix="/api/v1")
app.add_route("/metrics", metrics_endpoint)


@app.get("/health", tags=["Health"])
async def health():
    return {"status": "ok"}
