# direct_tools_agent.py
# pip install boto3 pydantic
#
# Required:
#   export AWS_REGION=eu-west-2
#   export BEDROCK_MODEL_ID=<your-bedrock-model-id-that-supports-tool-use>
#
# Run:
#   python direct_tools_agent.py success
#   python direct_tools_agent.py failure

import json
import logging
import os
import sys
from typing import Any, Callable

import boto3
from pydantic import BaseModel, Field, ValidationError, field_validator


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)

REGION = os.getenv("AWS_REGION", "eu-west-2")


# -------------------------
# Tool schemas
# -------------------------

class OrderTotalInput(BaseModel):
    sku: str = Field(..., min_length=3)
    quantity: int = Field(..., ge=1, le=100)
    unit_price: float = Field(..., gt=0)

    @field_validator("sku")
    @classmethod
    def validate_sku(cls, value: str) -> str:
        if not value.startswith("SKU-"):
            raise ValueError("sku must start with 'SKU-'")
        return value


class RefundEligibilityInput(BaseModel):
    order_id: str = Field(..., min_length=5)
    days_since_purchase: int = Field(..., ge=0)
    item_opened: bool

    @field_validator("order_id")
    @classmethod
    def validate_order_id(cls, value: str) -> str:
        if not value.startswith("ORD-"):
            raise ValueError("order_id must start with 'ORD-'")
        return value


# -------------------------
# Local Python tools
# -------------------------

def calculate_order_total(args: OrderTotalInput) -> dict[str, Any]:
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


# -------------------------
# Tool registry
# -------------------------

ToolHandler = Callable[[BaseModel], dict[str, Any]]

TOOLS: dict[str, dict[str, Any]] = {
    "calculate_order_total": {
        "description": "Calculate order subtotal, VAT, and total for a SKU.",
        "schema_model": OrderTotalInput,
        "handler": calculate_order_total,
    },
    "check_refund_eligibility": {
        "description": "Check whether an order is eligible for refund.",
        "schema_model": RefundEligibilityInput,
        "handler": check_refund_eligibility,
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


def execute_tool(tool_name: str, tool_input: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    logging.info("Tool selected by LLM: %s", tool_name)
    logging.info("Raw tool input: %s", json.dumps(tool_input))

    if tool_name not in TOOLS:
        return "error", {"error": f"Unknown tool: {tool_name}"}

    spec = TOOLS[tool_name]
    schema_model: type[BaseModel] = spec["schema_model"]
    handler: ToolHandler = spec["handler"]

    try:
        validated_input = schema_model.model_validate(tool_input)
        logging.info("Tool input validation passed")
        result = handler(validated_input)
        logging.info("Tool execution result: %s", json.dumps(result))
        return "success", result

    except ValidationError as exc:
        first_error = exc.errors()[0]
        error_location = ".".join(str(part) for part in first_error["loc"])
        error_message = first_error["msg"]

        if error_location == "sku" and "sku must start with" in error_message:
            logging.error("Validation failed: Invalid SKU format")
        else:
            logging.error("Validation failed: %s", error_message)

        return "error", {
            "error": "ValidationError",
            "details": [
                {
                    "field": ".".join(str(part) for part in error["loc"]),
                    "message": error["msg"],
                    "type": error["type"],
                    "input": error.get("input"),
                }
                for error in exc.errors()
            ],
        }

    except Exception as exc:
        logging.exception("Tool execution failed")
        return "error", {
            "error": type(exc).__name__,
            "message": str(exc),
        }


def run_agent(user_prompt: str, initial_tool_choice: str | None = None) -> str:
    model_id = os.getenv("BEDROCK_MODEL_ID")
    if not model_id:
        raise RuntimeError("Missing BEDROCK_MODEL_ID")

    client = boto3.client("bedrock-runtime", region_name=REGION)

    messages = [
        {
            "role": "user",
            "content": [{"text": user_prompt}],
        }
    ]

    response = client.converse(
        modelId=model_id,
        messages=messages,
        toolConfig=bedrock_tool_config(initial_tool_choice),
    )

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

            status, result = execute_tool(tool_name, tool_input)

            tool_results.append(
                {
                    "toolResult": {
                        "toolUseId": tool_use_id,
                        "status": status,
                        "content": [{"json": result}],
                    }
                }
            )

        messages.append(
            {
                "role": "user",
                "content": tool_results,
            }
        )

        response = client.converse(
            modelId=model_id,
            messages=messages,
            toolConfig=bedrock_tool_config(),
        )

        messages.append(response["output"]["message"])

    final_text = ""
    for block in response["output"]["message"]["content"]:
        if "text" in block:
            final_text += block["text"]

    return final_text


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "success"

    if mode == "success":
        prompt = (
            "Calculate the total price for SKU-BOOK-001. "
            "Quantity is 3 and unit price is 12.50. "
            "Use the available tool."
        )
        initial_tool_choice = "calculate_order_total"
    elif mode == "failure":
        prompt = (
            "Calculate the total price for BOOK-001. "
            "Quantity is 3 and unit price is 12.50. "
            "Use the available tool."
        )
        initial_tool_choice = "calculate_order_total"
    else:
        raise ValueError("Use either: success or failure")

    answer = run_agent(prompt, initial_tool_choice)
    print("\nAGENT RESPONSE:")
    print(answer)
