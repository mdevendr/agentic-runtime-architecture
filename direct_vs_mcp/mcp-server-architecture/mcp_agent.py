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
#   It launches mcp_server.py as a separate process.
#   Tool discovery and execution happen through MCP JSON-RPC over stdio.

import asyncio
import json
import logging
import os
import sys
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

SERVER_FILE = Path(__file__).parent / "mcp_server.py"


def to_bedrock_tool_config(mcp_tools: list[Any]) -> dict[str, Any]:
    """
    Converts MCP-discovered tools into Bedrock Converse tool definitions.

    The agent exposes only name, description, and JSON schema to the LLM.
    The executable implementation remains in the MCP server process.
    """
    bedrock_tools = []

    for tool in mcp_tools:
        bedrock_tools.append(
            {
                "toolSpec": {
                    "name": tool.name,
                    "description": tool.description or f"MCP tool {tool.name}",
                    "inputSchema": {
                        "json": tool.inputSchema,
                    },
                }
            }
        )

    return {"tools": bedrock_tools}


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


async def run_agent(mode: str) -> None:
    if not SERVER_FILE.exists():
        raise RuntimeError(f"MCP server file not found: {SERVER_FILE}")

    server_params = StdioServerParameters(
        command=sys.executable,
        args=[str(SERVER_FILE)],
    )

    logging.info("[AGENT] Starting MCP server as separate process")
    logging.info("[AGENT] Server command: %s %s", sys.executable, SERVER_FILE)

    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as mcp_client:
            logging.info("[AGENT] Initialising MCP session")
            await mcp_client.initialize()

            logging.info("[AGENT → MCP] JSON-RPC request: tools/list")
            tools_response = await mcp_client.list_tools()
            tools = tools_response.tools

            logging.info("[MCP → AGENT] tools/list returned %s tools", len(tools))

            for tool in tools:
                logging.info(
                    "[MCP TOOL DISCOVERED] name=%s description=%s schema=%s",
                    tool.name,
                    tool.description,
                    json.dumps(tool.inputSchema),
                )

            tool_config = to_bedrock_tool_config(tools)

            if mode == "success":
                prompt = (
                    "Calculate the total for SKU-BOOK-001. "
                    "Quantity is 3 and unit price is 12.50. "
                    "Use the available MCP tool."
                )
            elif mode == "failure":
                prompt = (
                    "Calculate the total for BOOK-001. "
                    "Quantity is 0 and unit price is -12.50. "
                    "Use the available MCP tool."
                )
            else:
                raise ValueError("mode must be success or failure")

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

                    logging.info(
                        "[AGENT → MCP] JSON-RPC request: tools/call params=%s",
                        json.dumps(
                            {
                                "name": tool_name,
                                "arguments": tool_input,
                            }
                        ),
                    )

                    try:
                        result = await mcp_client.call_tool(tool_name, tool_input)

                        logging.info("[MCP → AGENT] JSON-RPC response: tools/call result=%s", result)

                        status = "error" if result.isError else "success"

                        tool_results.append(
                            {
                                "toolResult": {
                                    "toolUseId": tool_use_id,
                                    "status": status,
                                    "content": [
                                        {
                                            "json": {
                                                "mcp_tool_result": str(result.content),
                                                "mcp_is_error": result.isError,
                                            }
                                        }
                                    ],
                                }
                            }
                        )

                    except Exception as exc:
                        logging.exception("[MCP → AGENT] JSON-RPC error from tools/call")

                        tool_results.append(
                            {
                                "toolResult": {
                                    "toolUseId": tool_use_id,
                                    "status": "error",
                                    "content": [
                                        {
                                            "json": {
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

                logging.info("[AGENT → LLM] Returning MCP tool result")
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
