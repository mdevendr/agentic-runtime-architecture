import json
import logging
from typing import Any


logger = logging.getLogger()
logger.setLevel(logging.INFO)


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    logger.info("[LAMBDA TARGET:check_refund_eligibility] event=%s", json.dumps(event))

    order_id = event.get("order_id")
    days_since_purchase = event.get("days_since_purchase")
    item_opened = event.get("item_opened")

    if not isinstance(order_id, str) or not order_id.startswith("ORD-"):
        raise ValueError("order_id must start with ORD-")

    if not isinstance(days_since_purchase, int) or days_since_purchase < 0:
        raise ValueError("days_since_purchase must be a non-negative integer")

    if not isinstance(item_opened, bool):
        raise ValueError("item_opened must be a boolean")

    eligible = days_since_purchase <= 30 and not item_opened
    return {
        "order_id": order_id,
        "eligible": eligible,
        "reason": (
            "Within 30 days and item unopened"
            if eligible
            else "Refund policy not satisfied"
        ),
    }
