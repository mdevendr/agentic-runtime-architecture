from __future__ import annotations

import argparse
import json
import logging
import os
from typing import Any

import boto3
from bedrock_agentcore import BedrockAgentCoreApp
from botocore.config import Config

from cognito_claims import verified_cognito_claims_for_user
from pooled_runtime_demo import handle_tool_request, tenant_context_from_verified_claims


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

AWS_REGION = os.getenv("AWS_REGION", "eu-west-2")
BEDROCK_CONNECT_TIMEOUT_SECONDS = int(os.getenv("BEDROCK_CONNECT_TIMEOUT_SECONDS", "10"))
BEDROCK_READ_TIMEOUT_SECONDS = int(os.getenv("BEDROCK_READ_TIMEOUT_SECONDS", "60"))
MAX_TOOL_ROUNDS = int(os.getenv("MAX_TOOL_ROUNDS", "3"))

app = BedrockAgentCoreApp()


TOOL_CONFIG = {
    "tools": [
        {
            "toolSpec": {
                "name": "check_order",
                "description": "Check order status and refund eligibility for the verified tenant.",
                "inputSchema": {
                    "json": {
                        "type": "object",
                        "properties": {
                            "order_id": {
                                "type": "string",
                                "description": "Tenant order id to inspect.",
                            }
                        },
                        "required": ["order_id"],
                    }
                },
            }
        },
        {
            "toolSpec": {
                "name": "create_refund",
                "description": "Create a refund for a tenant order when the verified tenant is authorized.",
                "inputSchema": {
                    "json": {
                        "type": "object",
                        "properties": {
                            "order_id": {
                                "type": "string",
                                "description": "Tenant order id to refund.",
                            }
                        },
                        "required": ["order_id"],
                    }
                },
            }
        },
    ]
}


def tool_config(tool_choice: str | None = None) -> dict[str, Any]:
    config = dict(TOOL_CONFIG)
    if tool_choice:
        config["toolChoice"] = {"tool": {"name": tool_choice}}
    return config


def call_bedrock(messages: list[dict[str, Any]], tool_choice: str | None = None) -> dict[str, Any]:
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
    return client.converse(
        modelId=model_id,
        messages=messages,
        toolConfig=tool_config(tool_choice),
    )


def build_verified_context(user_key: str, correlation_id: str):
    cognito_claims = verified_cognito_claims_for_user(
        user_key=user_key,
        correlation_id=correlation_id,
    )
    verified_context = tenant_context_from_verified_claims(
        cognito_claims.as_verified_context()
    )
    return cognito_claims, verified_context


def run_agent(
    prompt: str,
    user_key: str,
    correlation_id: str,
    initial_tool_choice: str | None = None,
) -> dict[str, Any]:
    cognito_claims, verified_context = build_verified_context(user_key, correlation_id)

    system_text = (
        "You are a tenant-aware order assistant. Use tools when order data or refund "
        "actions are needed. Tenant authorization is enforced by runtime policy, not "
        "by the prompt."
    )
    messages = [
        {"role": "user", "content": [{"text": f"{system_text}\n\nUser request: {prompt}"}]}
    ]

    logging.info(
        "multi_tenant_runtime_start tenant_id=%s subject=%s correlation_id=%s",
        verified_context.tenant_id,
        verified_context.subject,
        verified_context.correlation_id,
    )

    response = call_bedrock(messages, initial_tool_choice)
    messages.append(response["output"]["message"])

    tool_events: list[dict[str, Any]] = []
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

            outcome = handle_tool_request(
                verified_context=verified_context,
                requested_tool=tool_name,
                tool_input=tool_input,
            )

            status = "success" if outcome["allowed"] else "error"
            tool_events.append(
                {
                    "tool_use_id": tool_use_id,
                    "tool_name": tool_name,
                    "tool_input": tool_input,
                    "status": status,
                    "tenant_id": outcome["tenant_id"],
                    "authorization_decision": outcome["authorization_decision"],
                    "tool_executed": outcome["tool_executed"],
                    "result": outcome["result"],
                }
            )

            logging.info(
                "multi_tenant_tool_decision tenant_id=%s subject=%s tool=%s decision=%s executed=%s correlation_id=%s",
                outcome["tenant_id"],
                outcome["subject"],
                tool_name,
                outcome["authorization_decision"],
                outcome["tool_executed"],
                outcome["correlation_id"],
            )

            tool_results.append(
                {
                    "toolResult": {
                        "toolUseId": tool_use_id,
                        "status": status,
                        "content": [{"json": outcome}],
                    }
                }
            )

        if not tool_results:
            raise RuntimeError("Bedrock returned tool_use without toolUse content")

        messages.append({"role": "user", "content": tool_results})
        response = call_bedrock(messages)
        messages.append(response["output"]["message"])

    final_text = ""
    for block in response["output"]["message"]["content"]:
        if "text" in block:
            final_text += block["text"]

    return {
        "result": final_text,
        "cognito_subject": cognito_claims.sub,
        "cognito_tenant_claim": cognito_claims.tenant_id,
        "cognito_scope": cognito_claims.scope,
        "tenant_id": verified_context.tenant_id,
        "correlation_id": verified_context.correlation_id,
        "tool_events": tool_events,
    }


@app.entrypoint
def agentcore_entrypoint(request: dict[str, Any]) -> dict[str, Any]:
    prompt = request.get("prompt")
    if not prompt:
        return {"error": "Request must include prompt."}

    user_key = request.get("user_key", "tenant-a-user")
    correlation_id = request.get("correlation_id", f"corr-{user_key}")
    tool_choice = request.get("tool_choice")
    return run_agent(prompt, user_key, correlation_id, tool_choice)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run pooled multi-tenant Runtime with Bedrock Converse.")
    parser.add_argument("--user-key", default="tenant-a-user", choices=["tenant-a-user", "tenant-b-user"])
    parser.add_argument("--tool-choice", choices=["check_order", "create_refund"])
    parser.add_argument("--correlation-id", default="corr-local")
    parser.add_argument("prompt", nargs="*")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    if args.prompt:
        print(
            json.dumps(
                run_agent(
                    prompt=" ".join(args.prompt),
                    user_key=args.user_key,
                    correlation_id=args.correlation_id,
                    initial_tool_choice=args.tool_choice,
                ),
                indent=2,
            )
        )
    else:
        app.run()

