# Integrations Hub

Outgoing webhook delivery service with connector integrations. Publishes events via the outbox pattern, delivers webhooks with HMAC signing and retries, and includes a Slack connector for `request_submitted` events.

## Architecture

```
POST /api/v1/events  -->  outbox_events table  -->  delivery worker (async poll)
                                                       |
                                                       +--> webhook POST with HMAC sig
                                                       +--> retry with exponential backoff
                                                       +--> dead letter after max attempts

POST /api/v1/events (request_submitted)  -->  Slack connector  -->  Slack API
```

## Local Setup

### With Docker Compose

```bash
docker compose up --build
```

The service starts at `http://localhost:8000`. OpenAPI docs at `http://localhost:8000/docs`.

### Without Docker

Requirements: Python 3.11+, PostgreSQL 16+

```bash
# Create database
createdb integrations_hub

# Install
pip install -e ".[dev]"

# Run migrations
alembic upgrade head

# Start service
uvicorn integrations_hub.main:app --reload
```

## Environment Variables

All prefixed with `IH_`:

| Variable | Default | Description |
|----------|---------|-------------|
| `IH_DATABASE_URL` | `postgresql+asyncpg://postgres:postgres@localhost:5432/integrations_hub` | Async database URL |
| `IH_DATABASE_URL_SYNC` | `postgresql://postgres:postgres@localhost:5432/integrations_hub` | Sync database URL (Alembic) |
| `IH_DELIVERY_POLL_INTERVAL_SECONDS` | `2.0` | How often the worker polls the outbox |
| `IH_DELIVERY_MAX_ATTEMPTS` | `5` | Max delivery attempts before dead letter |
| `IH_DELIVERY_BACKOFF_BASE_SECONDS` | `2.0` | Base for exponential backoff (2^attempt) |
| `IH_DELIVERY_TIMEOUT_SECONDS` | `10.0` | HTTP timeout for webhook delivery |
| `IH_SLACK_BOT_TOKEN` | `""` | Slack Bot OAuth token |
| `IH_SLACK_DEFAULT_CHANNEL` | `#integrations` | Default Slack channel for notifications |
| `IH_LOG_LEVEL` | `INFO` | Logging level |

## API Examples

### Create a webhook subscription

```bash
curl -X POST http://localhost:8000/api/v1/subscriptions \
  -H "Content-Type: application/json" \
  -d '{
    "url": "https://your-service.com/webhook",
    "secret": "your-webhook-secret-key-min-16-chars",
    "events": ["request_submitted", "request_approved"],
    "enabled": true
  }'
```

### Publish an event

```bash
curl -X POST http://localhost:8000/api/v1/events \
  -H "Content-Type: application/json" \
  -d '{
    "event_type": "request_submitted",
    "payload": {
      "title": "Access to production DB",
      "requester": "alice@company.com",
      "description": "Need read access for quarterly report"
    }
  }'
```

### List delivery attempts for an event

```bash
curl http://localhost:8000/api/v1/admin/events/{event_id}/attempts
```

### Replay a dead-lettered event

```bash
curl -X POST http://localhost:8000/api/v1/admin/dead-letters/{dead_letter_id}/replay
```

## Webhook Payload Format

Delivered webhooks include these headers:

| Header | Description |
|--------|-------------|
| `X-Webhook-Signature` | HMAC-SHA256 hex digest of `{timestamp}.{payload}` |
| `X-Webhook-Timestamp` | Unix timestamp used in signature |
| `X-Webhook-Event` | Event type (e.g. `request_submitted`) |
| `X-Webhook-Event-Id` | Unique event ID |

Verify the signature:

```python
import hmac, hashlib

expected = hmac.new(
    secret.encode(),
    f"{timestamp}.{raw_body}".encode(),
    hashlib.sha256
).hexdigest()

assert hmac.compare_digest(expected, signature_header)
```

## Observability

- **Structured logs**: JSON via structlog to stdout
- **Metrics**: Prometheus-compatible at `GET /metrics`
- **Health check**: `GET /health`

## Testing

```bash
# Unit tests (no DB required)
pytest tests/test_signing.py tests/test_schemas.py tests/test_slack_connector.py tests/test_delivery.py -v

# Integration tests (requires Postgres)
pytest tests/test_api_integration.py -v

# All tests
pytest -v
```

## CI

GitHub Actions runs lint (ruff) and tests on every push/PR to `main`. See `.github/workflows/ci.yml`.
