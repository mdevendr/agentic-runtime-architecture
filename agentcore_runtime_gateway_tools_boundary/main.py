import argparse
import asyncio
import json
import logging
import os
from contextlib import AsyncExitStack
from typing import Any

import boto3
import httpx
from bedrock_agentcore import BedrockAgentCoreApp
from botocore.awsrequest import AWSRequest
from botocore.auth import SigV4Auth
from botocore.config import Config
from botocore.session import Session as BotocoreSession
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)

AWS_REGION = os.getenv("AWS_REGION", "eu-west-2")
BEDROCK_CONNECT_TIMEOUT_SECONDS = int(os.getenv("BEDROCK_CONNECT_TIMEOUT_SECONDS", "10"))
BEDROCK_READ_TIMEOUT_SECONDS = int(os.getenv("BEDROCK_READ_TIMEOUT_SECONDS", "60"))
GATEWAY_READ_TIMEOUT_SECONDS = int(os.getenv("GATEWAY_READ_TIMEOUT_SECONDS", "60"))
MAX_TOOL_ROUNDS = int(os.getenv("MAX_TOOL_ROUNDS", "5"))

app = BedrockAgentCoreApp()


class BedrockAgentCoreSigV4Auth(httpx.Auth):
    requires_request_body = True

    def __init__(self, region: str) -> None:
        self.region = region
        self.botocore_session = BotocoreSession()

    def auth_flow(self, request: httpx.Request):
        credentials = self.botocore_session.get_credentials()
        if credentials is None:
            raise RuntimeError("AWS credentials are required to invoke AgentCore Gateway")

        frozen = credentials.get_frozen_credentials()
        aws_request = AWSRequest(
            method=request.method,
            url=str(request.url),
            data=request.content,
            headers=dict(request.headers),
        )
        SigV4Auth(frozen, "bedrock-agentcore", self.region).add_auth(aws_request)
        request.headers.update(dict(aws_request.headers.items()))
        yield request


def gateway_url() -> str:
    value = os.getenv("AGENTCORE_GATEWAY_URL")
    if not value:
        raise RuntimeError("AGENTCORE_GATEWAY_URL is required")
    return value


def to_bedrock_tool_config(
    gateway_tools: list[Any],
    tool_choice: str | None = None,
) -> dict[str, Any]:
    config = {
        "tools": [
            {
                "toolSpec": {
                    "name": tool.name,
                    "description": tool.description or f"Gateway tool {tool.name}",
                    "inputSchema": {"json": tool.inputSchema},
                }
            }
            for tool in gateway_tools
        ]
    }

    if tool_choice:
        config["toolChoice"] = {"tool": {"name": tool_choice}}

    return config


def resolve_gateway_tool_choice(
    requested_tool_choice: str | None,
    gateway_tools: list[Any],
) -> str | None:
    if not requested_tool_choice:
        return None

    tool_names = [tool.name for tool in gateway_tools]
    if requested_tool_choice in tool_names:
        return requested_tool_choice

    suffix = f"___{requested_tool_choice}"
    matches = [name for name in tool_names if name.endswith(suffix)]
    if len(matches) == 1:
        logging.info(
            "[AGENT] Resolved requested tool %s to Gateway tool %s",
            requested_tool_choice,
            matches[0],
        )
        return matches[0]

    raise RuntimeError(
        f"Could not resolve requested Gateway tool {requested_tool_choice}. "
        f"Discovered tools: {tool_names}"
    )


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

    logging.info("[AGENT -> LLM] Sending prompt with Gateway-discovered tool schemas")
    return client.converse(
        modelId=model_id,
        messages=messages,
        toolConfig=tool_config,
    )


async def start_gateway_session(stack: AsyncExitStack) -> ClientSession:
    url = gateway_url()
    logging.info("[AGENT -> MCPCLIENT -> AGENTCORE GATEWAY] connect url=%s", url)

    auth_type = os.getenv("AGENTCORE_GATEWAY_AUTHORIZER", "AWS_IAM")
    auth = None if auth_type == "NONE" else BedrockAgentCoreSigV4Auth(AWS_REGION)
    http_client = httpx.AsyncClient(
        auth=auth,
        timeout=httpx.Timeout(GATEWAY_READ_TIMEOUT_SECONDS),
    )
    await stack.enter_async_context(http_client)

    read, write, get_session_id = await stack.enter_async_context(
        streamable_http_client(url, http_client=http_client)
    )
    session = await stack.enter_async_context(ClientSession(read, write))

    logging.info("[AGENT -> MCPCLIENT -> AGENTCORE GATEWAY] initialize")
    await session.initialize()
    logging.info(
        "[AGENTCORE GATEWAY -> MCPCLIENT -> AGENT] initialized mcp_session_id=%s",
        get_session_id(),
    )
    return session


async def discover_gateway_tools(session: ClientSession) -> list[Any]:
    logging.info("[AGENT -> MCPCLIENT -> AGENTCORE GATEWAY] tools/list")
    response = await session.list_tools()
    tools = response.tools
    logging.info(
        "[AGENTCORE GATEWAY -> MCPCLIENT -> AGENT] tools/list returned %s tools",
        len(tools),
    )
    for tool in tools:
        logging.info(
            "[GATEWAY TOOL DISCOVERED] name=%s description=%s schema=%s",
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


async def call_gateway_tool(
    session: ClientSession,
    tool_name: str,
    tool_input: dict[str, Any],
) -> tuple[str, dict[str, Any]]:
    logging.info(
        "[AGENT -> MCPCLIENT -> AGENTCORE GATEWAY -> TARGET] tools/call params=%s",
        json.dumps({"name": tool_name, "arguments": tool_input}),
    )

    try:
        result = await session.call_tool(tool_name, tool_input)
    except Exception as exc:
        logging.exception("[MCPCLIENT] Gateway tools/call failed")
        return "error", {
            "boundary": "agentcore_gateway",
            "gateway_url": gateway_url(),
            "gateway_tool": tool_name,
            "error": type(exc).__name__,
            "message": str(exc),
        }

    status = "error" if result.isError else "success"
    logging.info(
        "[TARGET -> AGENTCORE GATEWAY -> MCPCLIENT -> AGENT] tools/call status=%s result=%s",
        status,
        result,
    )

    return status, {
        "boundary": "agentcore_gateway",
        "gateway_url": gateway_url(),
        "gateway_tool": tool_name,
        "gateway_is_error": result.isError,
        "gateway_content": mcp_content_to_jsonable(result.content),
    }


async def run_gateway_tool_agent_async(
    prompt: str,
    initial_tool_choice: str | None = None,
) -> dict[str, Any]:
    async with AsyncExitStack() as stack:
        session = await start_gateway_session(stack)
        tools = await discover_gateway_tools(session)
        resolved_tool_choice = resolve_gateway_tool_choice(initial_tool_choice, tools)

        initial_tool_config = to_bedrock_tool_config(tools, resolved_tool_choice)
        followup_tool_config = to_bedrock_tool_config(tools)

        messages = [{"role": "user", "content": [{"text": prompt}]}]
        response = call_bedrock(messages, initial_tool_config)
        messages.append(response["output"]["message"])

        gateway_events: list[dict[str, Any]] = [
            {
                "event": "tools/list",
                "status": "success",
                "gateway_url": gateway_url(),
                "tools": [tool.name for tool in tools],
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

                status, result = await call_gateway_tool(session, tool_name, tool_input)
                gateway_events.append(
                    {
                        "event": "tools/call",
                        "tool_name": tool_name,
                        "status": status,
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
            logging.info("[AGENT -> LLM] Returning Gateway tool result")
            response = call_bedrock(messages, followup_tool_config)
            messages.append(response["output"]["message"])

        final_text = ""
        for block in response["output"]["message"]["content"]:
            if "text" in block:
                final_text += block["text"]

        return {
            "result": final_text,
            "gateway_events": gateway_events,
        }


def run_gateway_tool_agent(
    prompt: str,
    initial_tool_choice: str | None = None,
) -> dict[str, Any]:
    return asyncio.run(run_gateway_tool_agent_async(prompt, initial_tool_choice))


@app.entrypoint
def agentcore_entrypoint(request: dict[str, Any]) -> dict[str, Any]:
    prompt = request.get("prompt")
    if not prompt:
        return {"error": "Request must include a prompt field."}

    initial_tool_choice = request.get("tool_choice")
    return run_gateway_tool_agent(prompt, initial_tool_choice)


def parse_cli_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run the AgentCore Gateway-boundary agent once, or start the "
            "AgentCore app server when no prompt is supplied."
        )
    )
    parser.add_argument("--tool-choice", help="Force the first LLM call to use a Gateway tool.")
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
        one_shot_response = run_gateway_tool_agent(one_shot_prompt, args.tool_choice)
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
