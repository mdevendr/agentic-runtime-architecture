import argparse
import json
import os
from pathlib import Path
from typing import Any

import boto3


BASE_DIR = Path(__file__).parent


def shape_has_member(shape: Any, path: list[str]) -> bool:
    current = shape
    for part in path:
        if not current or getattr(current, "type_name", None) != "structure":
            return False
        members = getattr(current, "members", {})
        if part not in members:
            return False
        current = members[part]
    return True


def runtime_target_model_support(control_client) -> dict[str, Any]:
    service_model = control_client.meta.service_model
    create_gateway_target = service_model.operation_model("CreateGatewayTarget")
    target_config = create_gateway_target.input_shape.members["targetConfiguration"]

    return {
        "supports_http_target": shape_has_member(target_config, ["http"]),
        "supports_http_agentcore_runtime_target": shape_has_member(
            target_config,
            ["http", "agentcoreRuntime"],
        ),
        "supported_top_level_target_members": sorted(target_config.members.keys()),
    }


def desired_runtime_target_configuration(runtime_arn: str) -> dict[str, Any]:
    return {
        "http": {
            "agentcoreRuntime": {
                "arn": runtime_arn,
            }
        }
    }


def write_env_file(path: Path, values: dict[str, str]) -> None:
    path.write_text("\n".join(f"{key}={value}" for key, value in values.items()) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Prepare the Gateway-fronted Runtime scenario. This scenario is an "
            "alternative inbound Runtime front door: client -> AgentCore Gateway "
            "-> AgentCore Runtime target. It is separate from MCP Gateway tool targets."
        )
    )
    parser.add_argument("--profile", help="AWS profile to use.")
    parser.add_argument("--region", default=os.getenv("AWS_REGION", "eu-west-2"))
    parser.add_argument("--gateway-id", default=os.getenv("PROMPT4_RUNTIME_FRONTDOOR_GATEWAY_ID"))
    parser.add_argument("--runtime-arn", default=os.getenv("AGENT_RUNTIME_ARN") or os.getenv("AGENT_RUNTIME_ARN_IAM"))
    parser.add_argument(
        "--target-name",
        default=os.getenv("PROMPT4_RUNTIME_FRONTDOOR_TARGET_NAME", "prompt4RuntimeFrontdoor"),
    )
    parser.add_argument(
        "--env-file",
        default=str(BASE_DIR / "runtime_gateway_frontdoor.env"),
        help="Write non-secret setup outputs to this dotenv-style file when deploy support exists.",
    )
    args = parser.parse_args()

    session = boto3.Session(profile_name=args.profile) if args.profile else boto3.Session()
    control = session.client("bedrock-agentcore-control", region_name=args.region)
    support = runtime_target_model_support(control)

    desired_config = desired_runtime_target_configuration(args.runtime_arn or "<runtime-arn>")
    result: dict[str, Any] = {
        "scenario": "gateway_fronted_runtime",
        "implemented": False,
        "ready_for_deploy": False,
        "local_api_model_support": support,
        "required_inputs": {
            "PROMPT4_RUNTIME_FRONTDOOR_GATEWAY_ID": bool(args.gateway_id),
            "AGENT_RUNTIME_ARN or AGENT_RUNTIME_ARN_IAM": bool(args.runtime_arn),
        },
        "desired_boundary": "client -> AgentCore Gateway -> AgentCore Runtime target",
        "desired_target_configuration": desired_config,
        "notes": [
            "This is not an MCP Gateway tool target.",
            "It is an alternate front door where Gateway routes to a Runtime target.",
            "The installed boto3/botocore/AWS CLI model in this workspace currently exposes only MCP targetConfiguration members.",
            "Upgrade AWS SDK/CLI when the http.agentcoreRuntime target shape is available, then wire create_gateway_target using the desired target configuration.",
            "For the first concrete Gateway -> Runtime authorization mode, use gateway_fronted_runtime/iam_sigv4_setup.py.",
        ],
    }

    missing_inputs = [
        name
        for name, present in result["required_inputs"].items()
        if not present
    ]

    if support["supports_http_agentcore_runtime_target"] and not missing_inputs:
        result["implemented"] = True
        result["ready_for_deploy"] = True
        result["next_action"] = (
            "Local API model supports Runtime targets. Add the create_gateway_target "
            "call using desired_target_configuration before running this scenario."
        )
    elif missing_inputs:
        result["missing"] = missing_inputs
    else:
        result["missing"] = ["SDK/CLI support for targetConfiguration.http.agentcoreRuntime"]

    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
