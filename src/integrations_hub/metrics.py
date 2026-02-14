from prometheus_client import Counter, Histogram, generate_latest
from starlette.requests import Request
from starlette.responses import Response

WEBHOOK_DELIVERIES = Counter(
    "webhook_deliveries_total",
    "Total webhook delivery attempts",
    ["status"],
)
WEBHOOK_DELIVERY_DURATION = Histogram(
    "webhook_delivery_duration_seconds",
    "Webhook delivery duration in seconds",
)
EVENTS_PUBLISHED = Counter(
    "events_published_total",
    "Total events published to outbox",
    ["event_type"],
)
HTTP_REQUESTS = Counter(
    "http_requests_total",
    "Total HTTP requests",
    ["method", "path", "status_code"],
)


async def metrics_endpoint(request: Request) -> Response:
    return Response(content=generate_latest(), media_type="text/plain; charset=utf-8")
