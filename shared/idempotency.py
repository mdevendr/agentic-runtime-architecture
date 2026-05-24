from __future__ import annotations

import hashlib


def idempotency_key(session_id: str, message_id: str, tool_use_id: str) -> str:
    """Generate a stable key for the same model-selected mutating tool call."""

    material = f"{session_id}:{message_id}:{tool_use_id}".encode("utf-8")
    return hashlib.sha256(material).hexdigest()


class InMemoryIdempotencyStore:
    """
    Local evidence store for duplicate suppression.

    Production targets should enforce the same check at the mutation boundary
    with DynamoDB conditional writes, Redis SETNX, or an equivalent strongly
    consistent deduplication mechanism.
    """

    def __init__(self) -> None:
        self._results: dict[str, dict] = {}

    def record_once(self, key: str, result: dict) -> tuple[bool, dict]:
        if key in self._results:
            return False, self._results[key]

        self._results[key] = result
        return True, result

