# mcp_server.py
#
# Install:
#   pip install mcp
#
# Run directly for manual server test:
#   python mcp_server.py
#
# Normal execution:
#   This file is launched by mcp_agent.py as a separate child process
#   using stdio transport.

import logging
from typing import Any

from mcp.server.fastmcp import FastMCP


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)

mcp = FastMCP("order-mcp-server")


@mcp.tool()
def calculate_order_total(
    sku: str,
    quantity: int,
    unit_price: float,
) -> dict[str, Any]:
    """
    Calculate subtotal, VAT, and total for an order.

    Validation rules:
    - sku must start with SKU-
    - quantity must be >= 1
    - unit_price must be > 0
    """
    logging.info("[MCP SERVER] Executing calculate_order_total")

    if not sku.startswith("SKU-"):
        raise ValueError("sku must start with SKU-")

    if quantity < 1:
        raise ValueError("quantity must be greater than or equal to 1")

    if unit_price <= 0:
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


@mcp.tool()
def check_refund_eligibility(
    order_id: str,
    days_since_purchase: int,
    item_opened: bool,
) -> dict[str, Any]:
    """
    Check whether an order is eligible for refund.

    Validation rules:
    - order_id must start with ORD-
    - refund is allowed only within 30 days
    - item must not be opened
    """
    logging.info("[MCP SERVER] Executing check_refund_eligibility")

    if not order_id.startswith("ORD-"):
        raise ValueError("order_id must start with ORD-")

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


if __name__ == "__main__":
    logging.info("[MCP SERVER] Starting stdio server")
    mcp.run(transport="stdio")