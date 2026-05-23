import asyncio
import sys
from contextlib import AsyncExitStack
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def start_session(stack: AsyncExitStack, server_file: Path) -> ClientSession:
    params = StdioServerParameters(
        command=sys.executable,
        args=[str(server_file)],
    )
    read, write = await stack.enter_async_context(stdio_client(params))
    session = await stack.enter_async_context(ClientSession(read, write))
    await session.initialize()
    return session


async def main() -> None:
    async with AsyncExitStack() as stack:
        base_dir = Path(__file__).parent
        order_session = await start_session(stack, base_dir / "mcp_order_server.py")
        refund_session = await start_session(stack, base_dir / "mcp_refund_server.py")

        order_tools = await order_session.list_tools()
        refund_tools = await refund_session.list_tools()
        assert [tool.name for tool in order_tools.tools] == ["calculate_order_total"]
        assert [tool.name for tool in refund_tools.tools] == ["check_refund_eligibility"]

        success = await order_session.call_tool(
            "calculate_order_total",
            {"sku": "SKU-BOOK-001", "quantity": 3, "unit_price": 12.5},
        )
        assert not success.isError, success

        failure = await order_session.call_tool(
            "calculate_order_total",
            {"sku": "BOOK-001", "quantity": 3, "unit_price": 12.5},
        )
        assert failure.isError, failure

        refund = await refund_session.call_tool(
            "check_refund_eligibility",
            {"order_id": "ORD-1001", "days_since_purchase": 12, "item_opened": False},
        )
        assert not refund.isError, refund

        print("MCP smoke test passed: two servers, tools/list, and tools/call")


if __name__ == "__main__":
    asyncio.run(main())
