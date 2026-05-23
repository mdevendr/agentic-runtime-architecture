# mcp_order_server.py
#
# Owns the order calculation tool contract, validation, and execution.
# The agent launches this file as a separate stdio MCP server process.

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
    logging.info("[MCP ORDER SERVER] Executing calculate_order_total")

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


if __name__ == "__main__":
    logging.info("[MCP ORDER SERVER] Starting stdio server")
    mcp.run(transport="stdio")
