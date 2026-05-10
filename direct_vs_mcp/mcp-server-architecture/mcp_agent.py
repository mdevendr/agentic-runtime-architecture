# mcp_agent.py
#
# Install:
#   pip install mcp boto3
#
# Required:
#   export AWS_REGION=eu-west-2
#   export BEDROCK_MODEL_ID=<bedrock-model-id-that-supports-tool-use>
#
# Run success:
#   python mcp_agent.py success
#
# Run failure:
#   python mcp_agent.py failure
#
# Boundary proof:
#   This agent does NOT import tool functions.
#   It launches two independent MCP servers as separate processes.
#   Tool discovery and execution happen through MCP JSON-RPC over stdio.

import asyncio
import json
import logging
import os
import sys
from contextlib import AsyncExitStack
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import boto3
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)

AWS_REGION = os.getenv("AWS_REGION", "eu-west-2")
MODEL_ID = os.getenv("BEDROCK_MODEL_ID")

BASE_DIR = Path(__file__).parent
ORDER_SERVER_FILE = BASE_DIR / "mcp_order_server.py"
REFUND_SERVER_FILE = BASE_DIR / "mcp_refund_server.py"


@dataclass
class ToolOwner:
    label: str
    session: ClientSession


def to_bedrock_tool_config(mcp_tools: list[Any]) -> dict[str, Any]:
    """
    Converts MCP-discovered tools into Bedrock Converse tool definitions.

    The agent exposes only name, description, and JSON schema to the LLM.
    The executable implementation remains in the owning MCP server process.
    """
    return {
        "tools": [
            {
                "toolSpec": {
                    "name": tool.name,
                    "description": tool.description or f"MCP tool {tool.name}",
                    "inputSchema": {"json": tool.inputSchema},
                }
            }
            for tool in mcp_tools
        ]
    }


def call_bedrock(
    messages: list[dict[str, Any]],
    tool_config: dict[str, Any],
) -> dict[str, Any]:
    if not MODEL_ID:
        raise RuntimeError("Missing BEDROCK_MODEL_ID")

    client = boto3.client("bedrock-runtime", region_name=AWS_REGION)

    return client.converse(
        modelId=MODEL_ID,
        messages=messages,
        toolConfig=tool_config,
    )


def validate_server_files() -> None:
    for server_file in (ORDER_SERVER_FILE, REFUND_SERVER_FILE):
        if not server_file.exists():
            raise RuntimeError(f"MCP server file not found: {server_file}")


async def start_mcp_session(
    stack: AsyncExitStack,
    label: str,
    server_file: Path,
) -> ClientSession:
    server_params = StdioServerParameters(
        command=sys.executable,
        args=[str(server_file)],
    )

    logging.info("[AGENT] Starting MCP %s Server", label.title())
    logging.info("[AGENT] %s server command: %s %s", label, sys.executable, server_file)

    read, write = await stack.enter_async_context(stdio_client(server_params))
    session = await stack.enter_async_context(ClientSession(read, write))

    logging.info("[AGENT] Initialising %s MCP client session", label)
    await session.initialize()
    return session


async def discover_tools(
    label: str,
    session: ClientSession,
    tool_owners: dict[str, ToolOwner],
) -> list[Any]:
    logging.info("[AGENT → %s MCP] tools/list", label)
    tools_response = await session.list_tools()
    tools = tools_response.tools
    logging.info("[%s MCP → AGENT] tools/list returned %s tools", label, len(tools))

    for tool in tools:
        if tool.name in tool_owners:
            raise RuntimeError(f"Duplicate MCP tool name discovered: {tool.name}")

        tool_owners[tool.name] = ToolOwner(label=label, session=session)

        logging.info(
            "[MCP TOOL DISCOVERED] server=%s name=%s description=%s schema=%s",
            label,
            tool.name,
            tool.description,
            json.dumps(tool.inputSchema),
        )
        logging.info("[AGENT] Tool %s owned by %s MCP server", tool.name, label)

    return tools


def prompt_for_mode(mode: str) -> str:
    if mode == "success":
        return (
            "Use the available MCP tools to do both tasks. "
            "First calculate the order total for SKU-BOOK-001 with quantity 3 "
            "and unit price 12.50. Then check refund eligibility for order "
            "ORD-1001 purchased 12 days ago where the item is not opened."
        )

    if mode == "failure":
        return (
            "Use the available MCP tools to do both tasks. "
            "First calculate the order total for SKU-BOOK-001 with quantity 3 "
            "and unit price 12.50. Then check refund eligibility for invalid "
            "order id BAD-1001 purchased 12 days ago where the item is not opened."
        )

    raise ValueError("mode must be success or failure")


async def run_agent(mode: str) -> None:
    validate_server_files()

    async with AsyncExitStack() as stack:
        order_mcp_client = await start_mcp_session(stack, "ORDER", ORDER_SERVER_FILE)
        refund_mcp_client = await start_mcp_session(stack, "REFUND", REFUND_SERVER_FILE)

        tool_owners: dict[str, ToolOwner] = {}
        all_tools: list[Any] = []

        all_tools.extend(await discover_tools("ORDER", order_mcp_client, tool_owners))
        all_tools.extend(await discover_tools("REFUND", refund_mcp_client, tool_owners))

        tool_config = to_bedrock_tool_config(all_tools)
        prompt = prompt_for_mode(mode)

        messages = [
            {
                "role": "user",
                "content": [{"text": prompt}],
            }
        ]

        logging.info("[AGENT → LLM] Sending prompt with MCP-discovered tool schemas")
        response = call_bedrock(messages, tool_config)
        messages.append(response["output"]["message"])

        while response.get("stopReason") == "tool_use":
            tool_results = []

            for block in response["output"]["message"]["content"]:
                if "toolUse" not in block:
                    continue

                tool_use = block["toolUse"]
                tool_name = tool_use["name"]
                tool_input = tool_use["input"]
                tool_use_id = tool_use["toolUseId"]

                logging.info("[LLM → AGENT] Tool selected: %s", tool_name)
                logging.info("[LLM → AGENT] Tool arguments: %s", json.dumps(tool_input))

                owner = tool_owners.get(tool_name)
                if owner is None:
                    logging.error("[AGENT] No MCP owner found for tool %s", tool_name)
                    tool_results.append(
                        {
                            "toolResult": {
                                "toolUseId": tool_use_id,
                                "status": "error",
                                "content": [{"json": {"error": f"Unknown MCP tool: {tool_name}"}}],
                            }
                        }
                    )
                    continue

                logging.info("[AGENT] Routing tool %s to %s MCP server", tool_name, owner.label)
                logging.info(
                    "[AGENT → %s MCP] JSON-RPC request: tools/call params=%s",
                    owner.label,
                    json.dumps({"name": tool_name, "arguments": tool_input}),
                )

                try:
                    result = await owner.session.call_tool(tool_name, tool_input)

                    status = "error" if result.isError else "success"
                    if result.isError:
                        logging.error(
                            "[%s MCP → AGENT] JSON-RPC response: tools/call error=%s",
                            owner.label,
                            result,
                        )
                    else:
                        logging.info(
                            "[%s MCP → AGENT] JSON-RPC response: tools/call result=%s",
                            owner.label,
                            result,
                        )

                    tool_results.append(
                        {
                            "toolResult": {
                                "toolUseId": tool_use_id,
                                "status": status,
                                "content": [
                                    {
                                        "json": {
                                            "mcp_server": owner.label,
                                            "mcp_tool_result": str(result.content),
                                            "mcp_is_error": result.isError,
                                        }
                                    }
                                ],
                            }
                        }
                    )

                except Exception as exc:
                    logging.exception("[%s MCP → AGENT] JSON-RPC error from tools/call", owner.label)

                    tool_results.append(
                        {
                            "toolResult": {
                                "toolUseId": tool_use_id,
                                "status": "error",
                                "content": [
                                    {
                                        "json": {
                                            "mcp_server": owner.label,
                                            "error_type": type(exc).__name__,
                                            "message": str(exc),
                                        }
                                    }
                                ],
                            }
                        }
                    )

            messages.append(
                {
                    "role": "user",
                    "content": tool_results,
                }
            )

            logging.info("[AGENT → LLM] Returning MCP tool results")
            response = call_bedrock(messages, tool_config)
            messages.append(response["output"]["message"])

        final_text = ""
        for block in response["output"]["message"]["content"]:
            if "text" in block:
                final_text += block["text"]

        print("\nFINAL AGENT RESPONSE:")
        print(final_text)


if __name__ == "__main__":
    selected_mode = sys.argv[1] if len(sys.argv) > 1 else "success"
    asyncio.run(run_agent(selected_mode))
