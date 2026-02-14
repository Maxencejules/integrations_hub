from integrations_hub.services.signing import sign_payload, verify_signature


def test_sign_and_verify():
    payload = '{"event": "test"}'
    secret = "test-secret-key-1234"
    signature, timestamp = sign_payload(payload, secret)

    assert isinstance(signature, str)
    assert len(signature) == 64  # SHA256 hex digest
    assert isinstance(timestamp, int)
    assert verify_signature(payload, secret, signature, timestamp)


def test_verify_wrong_secret():
    payload = '{"event": "test"}'
    secret = "correct-secret-key-12"
    signature, timestamp = sign_payload(payload, secret)

    assert not verify_signature(payload, "wrong-secret-key-1234", signature, timestamp)


def test_verify_wrong_payload():
    payload = '{"event": "test"}'
    secret = "test-secret-key-1234"
    signature, timestamp = sign_payload(payload, secret)

    assert not verify_signature('{"event": "tampered"}', secret, signature, timestamp)


def test_deterministic_with_same_timestamp():
    payload = '{"event": "test"}'
    secret = "test-secret-key-1234"
    sig1, _ = sign_payload(payload, secret, timestamp=1000000)
    sig2, _ = sign_payload(payload, secret, timestamp=1000000)

    assert sig1 == sig2


def test_different_timestamps_produce_different_signatures():
    payload = '{"event": "test"}'
    secret = "test-secret-key-1234"
    sig1, _ = sign_payload(payload, secret, timestamp=1000000)
    sig2, _ = sign_payload(payload, secret, timestamp=1000001)

    assert sig1 != sig2
