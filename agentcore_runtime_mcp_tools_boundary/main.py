import argparse
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
from bedrock_agentcore import BedrockAgentCoreApp
from botocore.config import Config
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)

AWS_REGION = os.getenv("AWS_REGION", "eu-west-2")
BEDROCK_CONNECT_TIMEOUT_SECONDS = int(os.getenv("BEDROCK_CONNECT_TIMEOUT_SECONDS", "10"))
BEDROCK_READ_TIMEOUT_SECONDS = int(os.getenv("BEDROCK_READ_TIMEOUT_SECONDS", "60"))
MAX_TOOL_ROUNDS = int(os.getenv("MAX_TOOL_ROUNDS", "5"))

BASE_DIR = Path(__file__).parent
ORDER_SERVER_FILE = BASE_DIR / "mcp_order_server.py"
REFUND_SERVER_FILE = BASE_DIR / "mcp_refund_server.py"

app = BedrockAgentCoreApp()


@dataclass
class ToolOwner:
    label: str
    session: ClientSession


def validate_server_files() -> None:
    for server_file in (ORDER_SERVER_FILE, REFUND_SERVER_FILE):
        if not server_file.exists():
            raise RuntimeError(f"MCP server file not found: {server_file}")


def to_bedrock_tool_config(
    mcp_tools: list[Any],
    tool_choice: str | None = None,
) -> dict[str, Any]:
    config = {
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

    if tool_choice:
        config["toolChoice"] = {"tool": {"name": tool_choice}}

    return config


def call_bedrock(
    messages: list[dict[str, Any]],
    tool_config: dict[str, Any],
) -> dict[str, Any]:
    model_id = os.getenv("BEDROCK_MODEL_ID")
    if not model_id:
        raise RuntimeError("BEDROCK_MODEL_ID is required")

    client = boto3.client(
        "bedrock-runtime",
        region_name=AWS_REGION,
        config=Config(
            connect_timeout=BEDROCK_CONNECT_TIMEOUT_SECONDS,
            read_timeout=BEDROCK_READ_TIMEOUT_SECONDS,
            retries={"max_attempts": 2},
        ),
    )

    logging.info(
        "[AGENT -> LLM] Sending prompt with MCP-discovered tool schemas"
    )
    return client.converse(
        modelId=model_id,
        messages=messages,
        toolConfig=tool_config,
    )


async def start_mcp_session(
    stack: AsyncExitStack,
    label: str,
    server_file: Path,
) -> ClientSession:
    server_params = StdioServerParameters(
        command=sys.executable,
        args=[str(server_file)],
    )

    logging.info("[RUNTIME -> AGENT] Starting %s MCP server over stdio", label)
    logging.info(
        "[AGENT -> %s MCP TRANSPORT] command=%s args=%s",
        label,
        server_params.command,
        server_params.args,
    )

    read, write = await stack.enter_async_context(stdio_client(server_params))
    session = await stack.enter_async_context(ClientSession(read, write))

    logging.info("[AGENT -> %s MCPCLIENT] initialize", label)
    await session.initialize()
    logging.info("[%s MCPCLIENT -> AGENT] initialized", label)
    return session


async def discover_tools(
    label: str,
    session: ClientSession,
    tool_owners: dict[str, ToolOwner],
) -> list[Any]:
    logging.info("[AGENT -> %s MCPCLIENT -> STDIO -> %s MCP SERVER] tools/list", label, label)
    tools_response = await session.list_tools()
    tools = tools_response.tools
    logging.info(
        "[%s MCP SERVER -> STDIO -> %s MCPCLIENT -> AGENT] tools/list returned %s tools",
        label,
        label,
        len(tools),
    )

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

    return tools


def mcp_content_to_jsonable(content: list[Any]) -> list[dict[str, Any]]:
    jsonable = []
    for item in content:
        if hasattr(item, "model_dump"):
            jsonable.append(item.model_dump(mode="json"))
        else:
            jsonable.append({"text": str(item)})
    return jsonable


async def call_mcp_tool(
    owner: ToolOwner,
    tool_name: str,
    tool_input: dict[str, Any],
) -> tuple[str, dict[str, Any]]:
    logging.info("[AGENT] Routing tool %s to %s MCP server", tool_name, owner.label)
    logging.info(
        "[AGENT -> %s MCPCLIENT -> STDIO -> %s MCP SERVER] tools/call params=%s",
        owner.label,
        owner.label,
        json.dumps({"name": tool_name, "arguments": tool_input}),
    )

    try:
        result = await owner.session.call_tool(tool_name, tool_input)
    except Exception as exc:
        logging.exception("[MCPCLIENT] tools/call transport or server exception")
        return "error", {
            "boundary": "mcp",
            "mcp_server": owner.label,
            "mcp_tool": tool_name,
            "error": type(exc).__name__,
            "message": str(exc),
        }

    status = "error" if result.isError else "success"
    logging.info(
        "[%s MCP SERVER -> STDIO -> %s MCPCLIENT -> AGENT] tools/call status=%s result=%s",
        owner.label,
        owner.label,
        status,
        result,
    )

    return status, {
        "boundary": "mcp",
        "mcp_server": owner.label,
        "mcp_tool": tool_name,
        "mcp_is_error": result.isError,
        "mcp_content": mcp_content_to_jsonable(result.content),
    }


async def run_mcp_tool_agent_async(
    prompt: str,
    initial_tool_choice: str | None = None,
) -> dict[str, Any]:
    validate_server_files()

    async with AsyncExitStack() as stack:
        order_session = await start_mcp_session(stack, "ORDER", ORDER_SERVER_FILE)
        refund_session = await start_mcp_session(stack, "REFUND", REFUND_SERVER_FILE)

        tool_owners: dict[str, ToolOwner] = {}
        tools: list[Any] = []
        tools.extend(await discover_tools("ORDER", order_session, tool_owners))
        tools.extend(await discover_tools("REFUND", refund_session, tool_owners))
        initial_tool_config = to_bedrock_tool_config(tools, initial_tool_choice)
        followup_tool_config = to_bedrock_tool_config(tools)

        messages = [{"role": "user", "content": [{"text": prompt}]}]
        response = call_bedrock(messages, initial_tool_config)
        messages.append(response["output"]["message"])

        mcp_events: list[dict[str, Any]] = [
            {
                "event": "tools/list",
                "status": "success",
                "transport": "stdio",
                "tools": [
                    {
                        "name": tool.name,
                        "mcp_server": tool_owners[tool.name].label,
                    }
                    for tool in tools
                ],
            }
        ]

        tool_round = 0
        while response.get("stopReason") == "tool_use":
            tool_round += 1
            if tool_round > MAX_TOOL_ROUNDS:
                raise RuntimeError(f"Exceeded MAX_TOOL_ROUNDS={MAX_TOOL_ROUNDS}")

            tool_results = []

            for block in response["output"]["message"]["content"]:
                if "toolUse" not in block:
                    continue

                tool_use = block["toolUse"]
                tool_name = tool_use["name"]
                tool_input = tool_use["input"]
                tool_use_id = tool_use["toolUseId"]

                logging.info("[LLM -> AGENT] Tool selected: %s", tool_name)
                logging.info("[LLM -> AGENT] Tool arguments: %s", json.dumps(tool_input))

                owner = tool_owners.get(tool_name)
                if owner is None:
                    status = "error"
                    result = {
                        "boundary": "mcp",
                        "error": f"Unknown MCP tool: {tool_name}",
                    }
                else:
                    status, result = await call_mcp_tool(owner, tool_name, tool_input)

                mcp_events.append(
                    {
                        "event": "tools/call",
                        "tool_name": tool_name,
                        "status": status,
                        "transport": "stdio",
                        "input": tool_input,
                        "result": result,
                    }
                )

                tool_results.append(
                    {
                        "toolResult": {
                            "toolUseId": tool_use_id,
                            "status": status,
                            "content": [{"json": result}],
                        }
                    }
                )

            if not tool_results:
                raise RuntimeError("Bedrock returned stopReason=tool_use without toolUse content")

            messages.append({"role": "user", "content": tool_results})

            logging.info("[AGENT -> LLM] Returning MCP tool result")
            response = call_bedrock(messages, followup_tool_config)
            messages.append(response["output"]["message"])

        final_text = ""
        for block in response["output"]["message"]["content"]:
            if "text" in block:
                final_text += block["text"]

        return {
            "result": final_text,
            "mcp_events": mcp_events,
        }


def run_mcp_tool_agent(
    prompt: str,
    initial_tool_choice: str | None = None,
) -> dict[str, Any]:
    return asyncio.run(run_mcp_tool_agent_async(prompt, initial_tool_choice))


@app.entrypoint
def agentcore_entrypoint(request: dict[str, Any]) -> dict[str, Any]:
    prompt = request.get("prompt")
    if not prompt:
        return {"error": "Request must include a prompt field."}

    initial_tool_choice = request.get("tool_choice")
    return run_mcp_tool_agent(prompt, initial_tool_choice)


def parse_cli_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run the AgentCore MCP-boundary agent once, or start the AgentCore "
            "app server when no prompt is supplied."
        )
    )
    parser.add_argument(
        "--tool-choice",
        choices=["calculate_order_total", "check_refund_eligibility"],
        help="Force the LLM to call a specific MCP-backed tool.",
    )
    parser.add_argument(
        "prompt",
        nargs="*",
        help="Prompt to run once. Omit to start the AgentCore app server.",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_cli_args()
    if args.prompt:
        one_shot_prompt = " ".join(args.prompt)
        one_shot_response = run_mcp_tool_agent(one_shot_prompt, args.tool_choice)
        print(json.dumps(one_shot_response, indent=2))
    else:
        if args.tool_choice:
            raise RuntimeError("--tool-choice requires a prompt")
        logging.info("[RUNTIME] Starting local AgentCore app server")
        print(
            "Starting AgentCore local server on /invocations. "
            "This command keeps running until Ctrl+C.",
            flush=True,
        )
        app.run()
