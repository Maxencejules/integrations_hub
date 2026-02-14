# TASKS.md — integrations_hub

## Phase 1: Project Scaffolding
- [x] Create directory structure
- [x] pyproject.toml with dependencies
- [x] Dockerfile and docker-compose.yml
- [x] Config module (env-based)

## Phase 2: Database Layer
- [x] SQLAlchemy models: webhook_subscriptions, outbox_events, delivery_attempts, dead_letters
- [x] Alembic setup and initial migration
- [x] Database session management

## Phase 3: Webhook Subscriptions CRUD
- [x] Pydantic schemas
- [x] CRUD endpoints (POST, GET, PUT, DELETE /subscriptions)
- [x] Input validation

## Phase 4: Delivery Pipeline
- [x] Outbox writer: accept events, persist to outbox
- [x] Delivery worker: poll outbox, deliver with HMAC signing
- [x] Exponential backoff retry logic
- [x] Dead letter table after max attempts
- [x] Idempotency guard

## Phase 5: Security
- [x] HMAC-SHA256 payload signing
- [x] X-Signature and X-Timestamp headers

## Phase 6: Slack Connector
- [x] Slack connector service
- [x] Trigger on request_submitted events

## Phase 7: Admin Endpoints
- [x] POST /admin/dead-letters/{id}/replay
- [x] GET /admin/events/{id}/attempts

## Phase 8: Observability
- [x] Structured JSON logging
- [x] Prometheus /metrics endpoint

## Phase 9: Testing
- [x] Unit tests for signing, delivery, CRUD
- [x] Integration test with real DB

## Phase 10: CI & Docs
- [x] GitHub Actions CI workflow
- [x] OpenAPI (auto from FastAPI)
- [x] README with setup, env vars, examples
