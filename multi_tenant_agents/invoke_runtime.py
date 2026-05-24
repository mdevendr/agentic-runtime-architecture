from __future__ import annotations

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

    return "".join(chunk.decode("utf-8") for chunk in response.get("response", []))


def validate_evidence(response_text: str, expected_allowed: bool) -> None:
    payload = json.loads(response_text)
    tool_events = payload.get("tool_events", [])
    if not tool_events:
        print("ERROR: No tool_events returned. LLM tool-use was not evidenced.", file=sys.stderr)
        sys.exit(1)

    first_event = tool_events[0]
    if first_event.get("tool_executed") is not expected_allowed:
        print(
            "ERROR: Unexpected tool execution decision: "
            f"{first_event.get('tool_executed')} expected {expected_allowed}",
            file=sys.stderr,
        )
        sys.exit(1)

    print(
        "Verified multi-tenant Runtime decision: "
        f"tenant={first_event['tenant_id']} tool={first_event['tool_name']} "
        f"executed={first_event['tool_executed']} decision={first_event['authorization_decision']}",
        file=sys.stderr,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Invoke pooled multi-tenant Runtime with LLM.")
    parser.add_argument("--mode", choices=["tenant-a-refund", "tenant-b-check", "tenant-b-refund-denied"], default="tenant-a-refund")
    parser.add_argument("--session-id", default=f"multi-tenant-{uuid.uuid4()}")
    args = parser.parse_args()

    region = os.getenv("AWS_REGION", "eu-west-2")
    runtime_arn = require_env("MULTI_TENANT_RUNTIME_ARN")

    scenarios = {
        "tenant-a-refund": {
            "user_key": "tenant-a-user",
            "prompt": "Create a refund for order-1001 using the available tool.",
            "tool_choice": "create_refund",
            "expected_allowed": True,
        },
        "tenant-b-check": {
            "user_key": "tenant-b-user",
            "prompt": "Check order order-2001 using the available tool.",
            "tool_choice": "check_order",
            "expected_allowed": True,
        },
        "tenant-b-refund-denied": {
            "user_key": "tenant-b-user",
            "prompt": "Create a refund for order-2001 using the available tool.",
            "tool_choice": "create_refund",
            "expected_allowed": False,
        },
    }
    scenario = scenarios[args.mode]
    payload = {
        "prompt": scenario["prompt"],
        "user_key": scenario["user_key"],
        "tool_choice": scenario["tool_choice"],
        "correlation_id": f"corr-{args.mode}-{uuid.uuid4()}",
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
    validate_evidence(response_text, scenario["expected_allowed"])


if __name__ == "__main__":
    main()

