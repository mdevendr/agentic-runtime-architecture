import argparse
import json
import os
import sys
import uuid

import boto3


def require_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"{name} is required")
    return value


def read_response(response: dict) -> str:
    if "text/event-stream" in response.get("contentType", ""):
        chunks = []
        for line in response["response"].iter_lines(chunk_size=10):
            if line:
                text = line.decode("utf-8")
                if text.startswith("data: "):
                    text = text[6:]
                chunks.append(text)
        return "\n".join(chunks)

    chunks = []
    for chunk in response.get("response", []):
        chunks.append(chunk.decode("utf-8"))
    return "".join(chunks)


def validate_mcp_execution(response_text: str, mode: str) -> None:
    try:
        payload = json.loads(response_text)
    except json.JSONDecodeError:
        print(
            "ERROR: Runtime response was not JSON, so MCP execution could not be verified.",
            file=sys.stderr,
        )
        sys.exit(1)

    mcp_events = payload.get("mcp_events", [])
    if not mcp_events:
        print(
            "ERROR: Runtime response did not include any mcp_events. "
            "This does not prove MCP-backed tool execution.",
            file=sys.stderr,
        )
        sys.exit(1)

    list_events = [event for event in mcp_events if event.get("event") == "tools/list"]
    if not list_events or list_events[0].get("status") != "success":
        print("ERROR: Missing successful MCP tools/list event.", file=sys.stderr)
        sys.exit(1)

    call_events = [event for event in mcp_events if event.get("event") == "tools/call"]
    if not call_events:
        print("ERROR: Missing MCP tools/call event.", file=sys.stderr)
        sys.exit(1)

    expected_status = "success" if mode == "success" else "error"
    first_event = call_events[0]
    if first_event.get("tool_name") != "calculate_order_total":
        print(
            f"ERROR: Expected calculate_order_total, got {first_event.get('tool_name')}.",
            file=sys.stderr,
        )
        sys.exit(1)

    if first_event.get("status") != expected_status:
        print(
            f"ERROR: Expected first tool status {expected_status}, "
            f"got {first_event.get('status')}.",
            file=sys.stderr,
        )
        sys.exit(1)

    result = first_event.get("result", {})
    if result.get("boundary") != "mcp":
        print(
            f"ERROR: Expected result boundary=mcp, got {result.get('boundary')}.",
            file=sys.stderr,
        )
        sys.exit(1)

    print(
        f"Verified MCP tools/list and tools/call over {first_event['transport']}: "
        f"{first_event['tool_name']} returned status={first_event['status']}",
        file=sys.stderr,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Invoke the AgentCore MCP tools boundary runtime.")
    parser.add_argument("--mode", choices=["success", "failure"], default="success")
    parser.add_argument("--session-id", default=f"session-{uuid.uuid4()}")
    args = parser.parse_args()

    region = os.getenv("AWS_REGION", "eu-west-2")
    runtime_arn = require_env("AGENT_RUNTIME_ARN")

    if args.mode == "success":
        payload = {
            "prompt": (
                "Call calculate_order_total with exactly these arguments: "
                "sku is SKU-BOOK-001, quantity is 3, and unit_price is 12.50."
            ),
            "tool_choice": "calculate_order_total",
        }
    else:
        payload = {
            "prompt": (
                "Call calculate_order_total with exactly these arguments: "
                "sku is BOOK-001, quantity is 3, and unit_price is 12.50. "
                "Do not rewrite the SKU. Let tool validation determine whether the SKU is valid."
            ),
            "tool_choice": "calculate_order_total",
        }

    client = boto3.client("bedrock-agentcore", region_name=region)
    response = client.invoke_agent_runtime(
        agentRuntimeArn=runtime_arn,
        runtimeSessionId=args.session_id,
        contentType="application/json",
        accept="application/json",
        payload=json.dumps(payload).encode("utf-8"),
    )

    response_text = read_response(response)
    print(response_text)
    validate_mcp_execution(response_text, args.mode)


if __name__ == "__main__":
    main()
