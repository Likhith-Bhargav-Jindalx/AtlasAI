import base64
import hashlib
import hmac
import json
import time

from atlas_engine.config import settings


def _sign(payload_b64: bytes) -> str:
    signature = hmac.new(settings.session_secret.encode("utf-8"), payload_b64, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(signature).decode("ascii").rstrip("=")


def create_session_token(data: dict, *, max_age_seconds: int = 60 * 60 * 24 * 7) -> str:
    payload = {**data, "exp": int(time.time()) + max_age_seconds}
    payload_b64 = base64.urlsafe_b64encode(json.dumps(payload).encode("utf-8")).rstrip(b"=")
    signature = _sign(payload_b64)
    return f"{payload_b64.decode('ascii')}.{signature}"


def verify_session_token(token: str) -> dict | None:
    """Never raises — any malformed, tampered, or expired token just
    returns None, so callers can treat every failure mode identically."""
    try:
        payload_b64_str, signature = token.split(".", 1)
    except ValueError:
        return None

    payload_b64 = payload_b64_str.encode("ascii")
    expected_signature = _sign(payload_b64)
    if not hmac.compare_digest(signature, expected_signature):
        return None

    padded = payload_b64 + b"=" * (-len(payload_b64) % 4)
    try:
        payload = json.loads(base64.urlsafe_b64decode(padded))
    except Exception:
        return None

    if payload.get("exp", 0) < time.time():
        return None

    return payload
