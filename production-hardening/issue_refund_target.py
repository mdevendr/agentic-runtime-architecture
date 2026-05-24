from __future__ import annotations

import json
import logging
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from shared.idempotency import InMemoryIdempotencyStore


logger = logging.getLogger()
logger.setLevel(logging.INFO)

IDEMPOTENCY_STORE = InMemoryIdempotencyStore()


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """
    Evidence Lambda for mutation-boundary idempotency.

    This is not wired into the baseline Gateway deployment. It demonstrates the
    target-side rule from the article: the service that mutates state must check
    the idempotency key before committing the mutation.
    """

    logger.info("[LAMBDA TARGET:issue_refund] event=%s", json.dumps(event))
    idempotency_key = event.get("idempotency_key")
    correlation_id = event.get("correlation_id")
    order_id = event.get("order_id")
    amount = event.get("amount")

    if not isinstance(idempotency_key, str) or len(idempotency_key) < 32:
        raise ValueError("idempotency_key is required for mutating tools")

    if not isinstance(correlation_id, str) or not correlation_id:
        raise ValueError("correlation_id is required for mutating tools")

    if not isinstance(order_id, str) or not order_id.startswith("ORD-"):
        raise ValueError("order_id must start with ORD-")

    if not isinstance(amount, (int, float)) or amount <= 0:
        raise ValueError("amount must be greater than 0")

    result = {
        "order_id": order_id,
        "amount": amount,
        "status": "REFUND_ISSUED",
        "correlation_id": correlation_id,
    }

    created, stored_result = IDEMPOTENCY_STORE.record_once(idempotency_key, result)
    if not created:
        logger.info("[LAMBDA TARGET:issue_refund] duplicate suppressed key=%s", idempotency_key)
        return {
            **stored_result,
            "duplicate": True,
        }

    logger.info("[LAMBDA TARGET:issue_refund] mutation committed key=%s", idempotency_key)
    return {
        **stored_result,
        "duplicate": False,
    }
