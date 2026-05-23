import argparse
import json
import logging
import os
from typing import Any, Callable

import boto3
from bedrock_agentcore import BedrockAgentCoreApp
from botocore.config import Config
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)

AWS_REGION = os.getenv("AWS_REGION", "eu-west-2")
BEDROCK_CONNECT_TIMEOUT_SECONDS = int(os.getenv("BEDROCK_CONNECT_TIMEOUT_SECONDS", "10"))
BEDROCK_READ_TIMEOUT_SECONDS = int(os.getenv("BEDROCK_READ_TIMEOUT_SECONDS", "60"))
MAX_TOOL_ROUNDS = int(os.getenv("MAX_TOOL_ROUNDS", "5"))

app = BedrockAgentCoreApp()


class OrderTotalInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sku: str = Field(..., description="Product SKU. Must start with SKU-.")
    quantity: int = Field(..., ge=1, le=100)
    unit_price: float = Field(..., gt=0)

    @field_validator("sku")
    @classmethod
    def validate_sku(cls, value: str) -> str:
        if not value.startswith("SKU-"):
            raise ValueError("sku must start with SKU-")
        return value


class RefundEligibilityInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    order_id: str = Field(..., description="Order id. Must start with ORD-.")
    days_since_purchase: int = Field(..., ge=0)
    item_opened: bool

    @field_validator("order_id")
    @classmethod
    def validate_order_id(cls, value: str) -> str:
        if not value.startswith("ORD-"):
            raise ValueError("order_id must start with ORD-")
        return value


def calculate_order_total(args: OrderTotalInput) -> dict[str, Any]:
    logging.info("[DIRECT TOOL] Executing calculate_order_total in-process")

    subtotal = args.quantity * args.unit_price
    vat = round(subtotal * 0.20, 2)
    total = round(subtotal + vat, 2)

    return {
        "sku": args.sku,
        "quantity": args.quantity,
        "unit_price": args.unit_price,
        "subtotal": subtotal,
        "vat": vat,
        "total": total,
    }


def check_refund_eligibility(args: RefundEligibilityInput) -> dict[str, Any]:
    logging.info("[DIRECT TOOL] Executing check_refund_eligibility in-process")

    eligible = args.days_since_purchase <= 30 and not args.item_opened
    return {
        "order_id": args.order_id,
        "eligible": eligible,
        "reason": (
            "Within 30 days and item unopened"
            if eligible
            else "Refund policy not satisfied"
        ),
    }


ToolHandler = Callable[[BaseModel], dict[str, Any]]


def gateway_calculate_order_total(tool_name: str, tool_input: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """Per-tool gateway for calculate_order_total.

    This gateway can perform pre-processing, enrichment, and telemetry specific
    to the `calculate_order_total` tool before delegating to the shared
    `execute_tool` runner.
    """
    logging.info("[GATEWAY:calculate_order_total] Received request: %s", json.dumps(tool_input))

    # Example enrichment hook - ensure numeric types are properly coerced
    # (no-op if already correct)
    try:
        if "quantity" in tool_input:
            tool_input["quantity"] = int(tool_input["quantity"])
        if "unit_price" in tool_input:
            tool_input["unit_price"] = float(tool_input["unit_price"])
    except Exception:
        logging.exception("[GATEWAY:calculate_order_total] Failed to coerce numeric inputs")

    status, result = execute_tool(tool_name, tool_input)

    # Gateway-specific telemetry
    logging.info("[GATEWAY:calculate_order_total] Tool execution status: %s", status)
    return status, result


def gateway_check_refund_eligibility(tool_name: str, tool_input: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """Per-tool gateway for check_refund_eligibility.

    This gateway performs normalization and can attach audit/context data
    before delegating to the shared `execute_tool` function.
    """
    logging.info("[GATEWAY:check_refund_eligibility] Received request: %s", json.dumps(tool_input))

    # Normalization example
    if "item_opened" in tool_input and isinstance(tool_input["item_opened"], str):
        tool_input["item_opened"] = tool_input["item_opened"].lower() in ("true", "1", "yes")

    status, result = execute_tool(tool_name, tool_input)
    logging.info("[GATEWAY:check_refund_eligibility] Tool execution status: %s", status)
    return status, result


TOOLS: dict[str, dict[str, Any]] = {
    "calculate_order_total": {
        "description": "Calculate order subtotal, VAT, and total for a SKU.",
        "schema_model": OrderTotalInput,
        "handler": calculate_order_total,
        "gateway": gateway_calculate_order_total,
    },
    "check_refund_eligibility": {
        "description": "Check whether an order is eligible for refund.",
        "schema_model": RefundEligibilityInput,
        "handler": check_refund_eligibility,
        "gateway": gateway_check_refund_eligibility,
    },
}


def bedrock_tool_config(tool_choice: str | None = None) -> dict[str, Any]:
    config = {
        "tools": [
            {
                "toolSpec": {
                    "name": name,
                    "description": spec["description"],
                    "inputSchema": {
                        "json": spec["schema_model"].model_json_schema()
                    },
                }
            }
            for name, spec in TOOLS.items()
        ]
    }

    if tool_choice:
        config["toolChoice"] = {"tool": {"name": tool_choice}}

    return config


def sanitize_validation_error(exc: ValidationError) -> list[dict[str, Any]]:
    return [
        {
            "field": ".".join(str(part) for part in error["loc"]),
            "message": error["msg"],
            "type": error["type"],
            "input": error.get("input"),
        }
        for error in exc.errors()
    ]


def execute_tool(tool_name: str, tool_input: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    logging.info("[AGENT] Tool selected by LLM: %s", tool_name)
    logging.info("[AGENT] Raw tool input: %s", json.dumps(tool_input))

    if tool_name not in TOOLS:
        logging.error("[AGENT] LLM selected unknown tool: %s", tool_name)
        return "error", {"error": f"Unknown tool: {tool_name}"}

    spec = TOOLS[tool_name]
    schema_model: type[BaseModel] = spec["schema_model"]
    handler: ToolHandler = spec["handler"]

    try:
        validated_input = schema_model.model_validate(tool_input)
        logging.info("[AGENT] Tool input validation passed")
        result = handler(validated_input)
        logging.info("[AGENT] Tool execution result: %s", json.dumps(result))
        return "success", result

    except ValidationError as exc:
        first_error = sanitize_validation_error(exc)[0]
        logging.error(
            "[AGENT] Tool input validation failed: %s",
            first_error["message"],
        )
        return "error", {
            "error": "ValidationError",
            "details": sanitize_validation_error(exc),
        }

    except Exception as exc:
        logging.exception("[AGENT] Tool execution failed")
        return "error", {
            "error": type(exc).__name__,
            "message": str(exc),
        }


def call_bedrock(
    messages: list[dict[str, Any]],
    tool_choice: str | None = None,
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
        "[RUNTIME] Calling Bedrock model %s in %s with read timeout %ss",
        model_id,
        AWS_REGION,
        BEDROCK_READ_TIMEOUT_SECONDS,
    )
    return client.converse(
        modelId=model_id,
        messages=messages,
        toolConfig=bedrock_tool_config(tool_choice),
    )


def run_direct_tool_agent(
    prompt: str,
    initial_tool_choice: str | None = None,
) -> dict[str, Any]:
    messages = [{"role": "user", "content": [{"text": prompt}]}]

    logging.info("[RUNTIME] Sending prompt to LLM with direct tool schemas")
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

            # Route through per-tool gateway if present, otherwise fall back
            # to the shared execute_tool runner for backward compatibility.
            spec = TOOLS.get(tool_name)
            gateway = spec.get("gateway") if spec else None
            if callable(gateway):
                status, result = gateway(tool_name, tool_input)
            else:
                status, result = execute_tool(tool_name, tool_input)
            tool_events.append(
                {
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

        logging.info("[RUNTIME] Returning direct tool result to LLM")
        response = call_bedrock(messages)
        messages.append(response["output"]["message"])

    final_text = ""
    for block in response["output"]["message"]["content"]:
        if "text" in block:
            final_text += block["text"]

    return {
        "result": final_text,
        "tool_events": tool_events,
    }


@app.entrypoint
def agentcore_entrypoint(request: dict[str, Any]) -> dict[str, Any]:
    prompt = request.get("prompt")
    if not prompt:
        return {"error": "Request must include a prompt field."}

    initial_tool_choice = request.get("tool_choice")
    return run_direct_tool_agent(prompt, initial_tool_choice)


def parse_cli_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run the direct-tools agent once, or start the AgentCore app server "
            "when no prompt is supplied."
        )
    )
    parser.add_argument(
        "--tool-choice",
        choices=sorted(TOOLS),
        help="Force the LLM to call a specific direct tool.",
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
        prompt = " ".join(args.prompt)
        response = run_direct_tool_agent(prompt, args.tool_choice)
        print(json.dumps(response, indent=2))
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
