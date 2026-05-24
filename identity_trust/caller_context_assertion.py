from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from typing import Any


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def sign_caller_context(
    claims: dict[str, Any],
    secret: str,
    audience: str,
    ttl_seconds: int = 300,
) -> str:
    """
    Minimal signed caller-context assertion for substitution-mode evidence.

    This is intentionally small and dependency-free. Production systems should
    use managed OIDC/JWT signing, key rotation, issuer metadata, and JWKS.
    """

    now = int(time.time())
    header = {"alg": "HS256", "typ": "JWT"}
    payload = {
        **claims,
        "aud": audience,
        "iat": now,
        "exp": now + ttl_seconds,
    }
    signing_input = ".".join(
        [
            _b64url(json.dumps(header, separators=(",", ":")).encode("utf-8")),
            _b64url(json.dumps(payload, separators=(",", ":")).encode("utf-8")),
        ]
    )
    signature = hmac.new(secret.encode("utf-8"), signing_input.encode("ascii"), hashlib.sha256).digest()
    return f"{signing_input}.{_b64url(signature)}"


def verify_caller_context(token: str, secret: str, audience: str) -> dict[str, Any]:
    header_b64, payload_b64, signature_b64 = token.split(".")
    signing_input = f"{header_b64}.{payload_b64}"
    expected = hmac.new(secret.encode("utf-8"), signing_input.encode("ascii"), hashlib.sha256).digest()
    if not hmac.compare_digest(_b64url(expected), signature_b64):
        raise ValueError("caller context signature mismatch")

    padded_payload = payload_b64 + "=" * (-len(payload_b64) % 4)
    payload = json.loads(base64.urlsafe_b64decode(padded_payload.encode("ascii")))
    if payload.get("aud") != audience:
        raise ValueError("caller context audience mismatch")
    if int(payload.get("exp", 0)) < int(time.time()):
        raise ValueError("caller context assertion expired")
    return payload
