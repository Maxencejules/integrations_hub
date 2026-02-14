import hashlib
import hmac
import time


def sign_payload(payload: str, secret: str, timestamp: int | None = None) -> tuple[str, int]:
    """Sign a payload with HMAC-SHA256 and return (signature, timestamp)."""
    if timestamp is None:
        timestamp = int(time.time())
    message = f"{timestamp}.{payload}".encode()
    signature = hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()
    return signature, timestamp


def verify_signature(payload: str, secret: str, signature: str, timestamp: int) -> bool:
    """Verify an HMAC-SHA256 signature."""
    expected, _ = sign_payload(payload, secret, timestamp)
    return hmac.compare_digest(expected, signature)
