import json
import os
import uuid
from typing import Any

import boto3
from botocore.exceptions import ClientError


def read_response(response: dict[str, Any]) -> str:
    chunks = []
    for chunk in response.get("response", []):
        chunks.append(chunk.decode("utf-8"))
    return "".join(chunks)


def handler(event: dict[str, Any], _context: Any) -> dict[str, Any]:
    runtime_arn = event.get("runtime_arn") or os.getenv("AGENT_RUNTIME_ARN")
    if not runtime_arn:
        raise RuntimeError("AGENT_RUNTIME_ARN or event.runtime_arn is required")

    region = os.getenv("AWS_REGION", "eu-west-2")
    client = boto3.client("bedrock-agentcore", region_name=region)
    payload = {
        "prompt": (
            "Call calculate_order_total with exactly these arguments: "
            "sku is SKU-BOOK-001, quantity is 3, and unit_price is 12.50."
        ),
        "tool_choice": "calculate_order_total",
    }

    try:
        response = client.invoke_agent_runtime(
            agentRuntimeArn=runtime_arn,
            runtimeSessionId=f"lambda-workload-{uuid.uuid4()}",
            contentType="application/json",
            accept="application/json",
            payload=json.dumps(payload).encode("utf-8"),
        )
    except ClientError as exc:
        return {
            "ok": False,
            "error_type": type(exc).__name__,
            "error_code": exc.response.get("Error", {}).get("Code"),
            "error": str(exc),
        }

    return {
        "ok": True,
        "runtime_response": read_response(response),
    }
