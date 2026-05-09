# mcp_refund_server.py
#
# Owns the refund eligibility tool contract, validation, and execution.
# The agent launches this file as a separate stdio MCP server process.

import logging
from typing import Any

from mcp.server.fastmcp import FastMCP


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)

mcp = FastMCP("refund-mcp-server")


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
    logging.info("[MCP REFUND SERVER] Executing check_refund_eligibility")

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
    logging.info("[MCP REFUND SERVER] Starting stdio server")
    mcp.run(transport="stdio")
