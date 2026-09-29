"""Exercise API -> PostgreSQL outbox -> worker -> real HTTP receiver.

Use a dedicated, migrated demo database. The subscription and event remain there
for inspection. No Slack notification or external HTTP request is made.
"""

import asyncio
import hashlib
import hmac
import json
import os
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

import httpx

if "IH_DATABASE_URL" not in os.environ:
    raise SystemExit("Set IH_DATABASE_URL to a dedicated, migrated demo database first.")

from integrations_hub.config import settings  # noqa: E402
from integrations_hub.database import engine  # noqa: E402
from integrations_hub.main import app  # noqa: E402


async def main() -> None:
    if settings.delivery_max_attempts < 2:
        raise SystemExit("This demo needs IH_DELIVERY_MAX_ATTEMPTS >= 2.")
    secret = secrets.token_hex(32)
    receipts: list[dict] = []
    errors: list[str] = []

    class Receiver(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            body = self.rfile.read(int(self.headers["Content-Length"]))
            timestamp = self.headers.get("X-Webhook-Timestamp", "")
            expected = hmac.new(
                secret.encode(), timestamp.encode() + b"." + body, hashlib.sha256
            ).hexdigest()
            if not hmac.compare_digest(expected, self.headers.get("X-Webhook-Signature", "")):
                errors.append("Receiver rejected an invalid signature")
                status = 401
            else:
                envelope = json.loads(body)
                receipts.append(envelope)
                status = 503 if len(receipts) == 1 else 200
            self.send_response(status)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, *_args) -> None:
            pass

    receiver = HTTPServer(("127.0.0.1", 0), Receiver)
    thread = threading.Thread(target=receiver.serve_forever, daemon=True)
    thread.start()
    payload = {"demo": "signed retry", "message": "café", "nested": {"approved": True}}
    try:
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://demo"
            ) as client:
                subscription = await client.post(
                    "/api/v1/subscriptions",
                    json={
                        "url": f"http://127.0.0.1:{receiver.server_port}/webhook",
                        "secret": secret,
                        "events": ["request_approved"],
                    },
                )
                subscription.raise_for_status()
                event = await client.post(
                    "/api/v1/events", json={"event_type": "request_approved", "payload": payload}
                )
                event.raise_for_status()
                event_id = event.json()["id"]
                deadline = time.monotonic() + 30
                while time.monotonic() < deadline:
                    if errors:
                        raise RuntimeError(errors[0])
                    response = await client.get(f"/api/v1/admin/events/{event_id}/attempts")
                    response.raise_for_status()
                    attempts = [
                        a
                        for a in response.json()
                        if a["subscription_id"] == subscription.json()["id"]
                    ]
                    if any(a["status"] == "delivered" for a in attempts):
                        break
                    await asyncio.sleep(0.1)
                else:
                    raise RuntimeError("Delivery did not succeed within 30 seconds")
                attempts.sort(key=lambda a: a["attempt_number"])
                outcomes = [(a["status"], a["http_status_code"]) for a in attempts]
                if outcomes != [("failed", 503), ("delivered", 200)]:
                    raise RuntimeError(f"Unexpected delivery outcomes: {outcomes}")
                if len(receipts) != 2 or any(r["data"] != payload for r in receipts):
                    raise RuntimeError("Receiver payload did not match the published event")
                if any(r["event_id"] != event_id for r in receipts):
                    raise RuntimeError("The event ID changed across retries")
                print(
                    json.dumps(
                        {
                            "event_id": event_id,
                            "signature_verified": True,
                            "payload_verified": True,
                            "attempts": outcomes,
                        },
                        indent=2,
                    )
                )
    finally:
        await asyncio.to_thread(receiver.shutdown)
        thread.join()
        receiver.server_close()
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
