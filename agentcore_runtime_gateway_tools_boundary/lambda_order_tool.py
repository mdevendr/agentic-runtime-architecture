import json
import logging
from typing import Any


logger = logging.getLogger()
logger.setLevel(logging.INFO)


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    logger.info("[LAMBDA TARGET] event=%s", json.dumps(event))

    sku = event.get("sku")
    quantity = event.get("quantity")
    unit_price = event.get("unit_price")

    if not isinstance(sku, str) or not sku.startswith("SKU-"):
        raise ValueError("sku must start with SKU-")

    if not isinstance(quantity, int) or quantity < 1:
        raise ValueError("quantity must be an integer greater than or equal to 1")

    if not isinstance(unit_price, (int, float)) or unit_price <= 0:
        raise ValueError("unit_price must be greater than 0")

    subtotal = quantity * unit_price
    vat = round(subtotal * 0.20, 2)
    total = round(subtotal + vat, 2)

    return {
        "sku": sku,
        "quantity": quantity,
        "unit_price": unit_price,
        "subtotal": subtotal,
        "vat": vat,
        "total": total,
    }
