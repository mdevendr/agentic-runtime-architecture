import argparse
import json
import os
import re
import time
from pathlib import Path
from typing import Any

import boto3
from botocore.exceptions import ClientError


BASE_DIR = Path(__file__).resolve().parent
IDENTITY_DIR = BASE_DIR.parent

GATEWAY_NAME_PATTERN = re.compile(r"^([0-9a-zA-Z]-?){1,48}$")
TARGET_NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
ROLE_NAME_PATTERN = re.compile(r"^[\w+=,.@-]{1,64}$")


def validate_name(name: str, label: str, pattern: re.Pattern[str], rule: str) -> str:
    if not pattern.fullmatch(name):
        raise RuntimeError(f"{label} must match {rule}.")
    return name


def get_account_id(session: boto3.Session) -> str:
    return session.client("sts").get_caller_identity()["Account"]


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


def local_model_support(control) -> dict[str, Any]:
    service_model = control.meta.service_model
    create_gateway = service_model.operation_model("CreateGateway")
    create_target = service_model.operation_model("CreateGatewayTarget")
    target_config = create_target.input_shape.members["targetConfiguration"]

    gateway_members = sorted(create_gateway.input_shape.members.keys())
    gateway_required = sorted(create_gateway.input_shape.required_members)
    target_members = sorted(target_config.members.keys())

    return {
        "create_gateway_required_members": gateway_required,
        "create_gateway_members": gateway_members,
        "supports_gateway_without_protocol_type": "protocolType" not in gateway_required,
        "create_gateway_target_top_level_members": target_members,
        "supports_http_target": shape_has_member(target_config, ["http"]),
        "supports_http_agentcore_runtime_target": shape_has_member(
            target_config,
            ["http", "agentcoreRuntime"],
        ),
    }


def runtime_resources(runtime_arn: str) -> list[str]:
    return [
        runtime_arn,
        f"{runtime_arn}/runtime-endpoint/*",
    ]


def runtime_target_configuration(runtime_arn: str, qualifier: str | None = None) -> dict[str, Any]:
    target: dict[str, Any] = {
        "http": {
            "agentcoreRuntime": {
                "arn": runtime_arn,
            }
        }
    }
    if qualifier:
        target["http"]["agentcoreRuntime"]["qualifier"] = qualifier
    return target


def credential_provider_configuration() -> list[dict[str, str]]:
    return [{"credentialProviderType": "GATEWAY_IAM_ROLE"}]


def gateway_role_trust_policy(account_id: str, region: str) -> dict[str, Any]:
    return {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Principal": {"Service": "bedrock-agentcore.amazonaws.com"},
                "Action": "sts:AssumeRole",
                "Condition": {
                    "StringEquals": {"aws:SourceAccount": account_id},
                    "ArnLike": {
                        "aws:SourceArn": f"arn:aws:bedrock-agentcore:{region}:{account_id}:*"
                    },
                },
            }
        ],
    }


def gateway_runtime_invoke_policy(runtime_arn: str) -> dict[str, Any]:
    return {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Sid": "GatewayInvokeRuntimeTarget",
                "Effect": "Allow",
                "Action": ["bedrock-agentcore:InvokeAgentRuntime"],
                "Resource": runtime_resources(runtime_arn),
            }
        ],
    }


def ensure_gateway_role(
    session: boto3.Session,
    role_name: str,
    runtime_arn: str,
    account_id: str,
    region: str,
) -> str:
    iam = session.client("iam")
    trust_policy = gateway_role_trust_policy(account_id, region)

    try:
        role = iam.get_role(RoleName=role_name)["Role"]
        print(f"Using existing Gateway front-door role: {role['Arn']}")
        iam.update_assume_role_policy(
            RoleName=role_name,
            PolicyDocument=json.dumps(trust_policy),
        )
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") != "NoSuchEntity":
            raise
        role = iam.create_role(
            RoleName=role_name,
            AssumeRolePolicyDocument=json.dumps(trust_policy),
            Description="AgentCore Gateway role for IAM/SigV4 invocation of Runtime target.",
            Tags=[{"Key": "ArchitecturePattern", "Value": "GatewayFrontedRuntime"}],
        )["Role"]
        print(f"Created Gateway front-door role: {role['Arn']}")

    iam.put_role_policy(
        RoleName=role_name,
        PolicyName="AllowInvokeAgentCoreRuntimeTarget",
        PolicyDocument=json.dumps(gateway_runtime_invoke_policy(runtime_arn)),
    )
    iam.get_waiter("role_exists").wait(RoleName=role_name)
    time.sleep(10)
    return role["Arn"]


def create_runtime_frontdoor_gateway(
    control,
    gateway_name: str,
    role_arn: str,
    authorizer_type: str,
) -> dict[str, Any]:
    try:
        response = control.create_gateway(
            name=gateway_name,
            description="Prompt 4 Gateway-fronted Runtime using Gateway service role IAM/SigV4.",
            roleArn=role_arn,
            authorizerType=authorizer_type,
            exceptionLevel="DEBUG",
            tags={
                "ArchitecturePattern": "GatewayFrontedRuntime",
                "TargetAuthMode": "GatewayServiceRoleIamSigV4",
            },
        )
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") != "ConflictException":
            raise
        existing = find_gateway_by_name(control, gateway_name)
        if not existing:
            raise RuntimeError(
                f"Gateway {gateway_name} already exists but was not returned by ListGateways."
            ) from exc
        print(f"Using existing Gateway: {existing['gatewayArn']}")
        return existing

    print("Gateway create response:")
    print(json.dumps(response, indent=2, default=str))
    return response


def find_gateway_by_name(control, gateway_name: str) -> dict[str, Any] | None:
    request: dict[str, Any] = {}
    while True:
        response = control.list_gateways(**request)
        for gateway in response.get("items", []):
            if gateway.get("name") == gateway_name:
                return control.get_gateway(gatewayIdentifier=gateway["gatewayId"])
        next_token = response.get("nextToken")
        if not next_token:
            return None
        request["nextToken"] = next_token


def wait_for_gateway_ready(control, gateway_id: str) -> dict[str, Any]:
    while True:
        response = control.get_gateway(gatewayIdentifier=gateway_id)
        status = response["status"]
        print(f"Gateway status: {status}")
        if status == "READY":
            return response
        if status in {"CREATE_FAILED", "UPDATE_FAILED", "DELETING"}:
            raise RuntimeError(f"Gateway did not become READY: {response}")
        time.sleep(10)


def create_runtime_target(
    control,
    gateway_id: str,
    target_name: str,
    runtime_arn: str,
    qualifier: str | None,
) -> dict[str, Any]:
    try:
        response = control.create_gateway_target(
            gatewayIdentifier=gateway_id,
            name=target_name,
            description="AgentCore Runtime target authorized with Gateway service role IAM/SigV4.",
            targetConfiguration=runtime_target_configuration(runtime_arn, qualifier),
            credentialProviderConfigurations=credential_provider_configuration(),
        )
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") != "ConflictException":
            raise
        existing = find_target_by_name(control, gateway_id, target_name)
        if not existing:
            raise RuntimeError(
                f"Gateway target {target_name} already exists but was not returned by ListGatewayTargets."
            ) from exc
        print(f"Using existing Runtime target: {existing['targetId']}")
        return existing

    print("Runtime target create response:")
    print(json.dumps(response, indent=2, default=str))
    return response


def find_target_by_name(control, gateway_id: str, target_name: str) -> dict[str, Any] | None:
    request: dict[str, Any] = {"gatewayIdentifier": gateway_id}
    while True:
        response = control.list_gateway_targets(**request)
        for target in response.get("items", []):
            if target.get("name") == target_name:
                return control.get_gateway_target(
                    gatewayIdentifier=gateway_id,
                    targetId=target["targetId"],
                )
        next_token = response.get("nextToken")
        if not next_token:
            return None
        request["nextToken"] = next_token


def wait_for_target_ready(control, gateway_id: str, target_id: str) -> dict[str, Any]:
    while True:
        response = control.get_gateway_target(
            gatewayIdentifier=gateway_id,
            targetId=target_id,
        )
        status = response["status"]
        print(f"Gateway target status: {status}")
        if status == "READY":
            return response
        if status in {"CREATE_FAILED", "UPDATE_FAILED", "DELETING"}:
            raise RuntimeError(f"Gateway target did not become READY: {response}")
        time.sleep(10)


def write_env_file(path: Path, values: dict[str, str]) -> None:
    path.write_text(
        "\n".join(f"{key}={value}" for key, value in values.items()) + "\n",
        encoding="utf-8",
    )


def readiness_result(args: argparse.Namespace, support: dict[str, Any]) -> dict[str, Any]:
    has_api_support = (
        support["supports_gateway_without_protocol_type"]
        and support["supports_http_agentcore_runtime_target"]
    )
    has_runtime_arn = bool(args.runtime_arn)
    return {
        "scenario": "gateway_fronted_runtime_iam_sigv4",
        "implemented": has_api_support,
        "ready_for_deploy": has_api_support and has_runtime_arn,
        "reason": (
            "SDK model supports Gateway HTTP Runtime targets."
            if has_api_support
            else "Installed boto3/botocore model does not expose Gateway HTTP Runtime targets yet."
        ),
        "local_api_model_support": support,
        "required_inputs": {
            "AGENT_RUNTIME_ARN_IAM or AGENT_RUNTIME_ARN": has_runtime_arn,
        },
        "desired_gateway": {
            "name": args.gateway_name,
            "authorizerType": args.authorizer_type,
            "protocolType": None,
        },
        "desired_target_configuration": runtime_target_configuration(
            args.runtime_arn or "<runtime-arn>",
            args.qualifier,
        ),
        "desired_credential_provider_configurations": credential_provider_configuration(),
        "notes": [
            "This is the first Gateway -> Runtime target authorization mode in the diagram.",
            "Gateway is the front door; Runtime remains the target.",
            "Gateway uses its own service role to SigV4-sign InvokeAgentRuntime.",
            "The Gateway service role policy must allow bedrock-agentcore:InvokeAgentRuntime on the Runtime ARN and runtime endpoints.",
            "The existing MCP Gateway used by Runtime tools is intentionally not reused.",
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Create Gateway-fronted Runtime mode 1: Gateway service role IAM/SigV4."
    )
    parser.add_argument("--profile", help="AWS profile to use.")
    parser.add_argument("--region", default=os.getenv("AWS_REGION", "eu-west-2"))
    parser.add_argument(
        "--runtime-arn",
        default=os.getenv("AGENT_RUNTIME_ARN_IAM") or os.getenv("AGENT_RUNTIME_ARN"),
        help="Runtime ARN that the Gateway target should invoke.",
    )
    parser.add_argument(
        "--qualifier",
        default=os.getenv("PROMPT4_RUNTIME_TARGET_QUALIFIER"),
        help="Optional Runtime target qualifier/version when supported.",
    )
    parser.add_argument(
        "--gateway-name",
        default=os.getenv("PROMPT4_RUNTIME_FRONTDOOR_IAM_GATEWAY_NAME", "prompt4-runtime-frontdoor-iam"),
    )
    parser.add_argument(
        "--target-name",
        default=os.getenv("PROMPT4_RUNTIME_FRONTDOOR_IAM_TARGET_NAME", "prompt4RuntimeIamSigv4Target"),
    )
    parser.add_argument(
        "--role-name",
        default=os.getenv("PROMPT4_RUNTIME_FRONTDOOR_IAM_ROLE_NAME", "Prompt4RuntimeFrontdoorIamRole"),
    )
    parser.add_argument(
        "--authorizer-type",
        default=os.getenv("PROMPT4_RUNTIME_FRONTDOOR_AUTHORIZER", "AWS_IAM"),
        choices=["AWS_IAM", "CUSTOM_JWT"],
        help="Inbound Gateway authorizer. This scenario keeps Runtime target auth as Gateway IAM/SigV4.",
    )
    parser.add_argument(
        "--env-file",
        default=str(IDENTITY_DIR / "gateway_fronted_runtime_iam.env"),
    )
    parser.add_argument(
        "--check-only",
        action="store_true",
        help="Only print SDK support and desired configuration.",
    )
    args = parser.parse_args()

    args.gateway_name = validate_name(
        args.gateway_name,
        "Gateway name",
        GATEWAY_NAME_PATTERN,
        "([0-9a-zA-Z]-?){1,48}",
    )
    args.target_name = validate_name(
        args.target_name,
        "Target name",
        TARGET_NAME_PATTERN,
        "[A-Za-z0-9][A-Za-z0-9_-]{0,63}",
    )
    args.role_name = validate_name(
        args.role_name,
        "Role name",
        ROLE_NAME_PATTERN,
        "[\\w+=,.@-]{1,64}",
    )

    session = boto3.Session(profile_name=args.profile) if args.profile else boto3.Session()
    control = session.client("bedrock-agentcore-control", region_name=args.region)
    support = local_model_support(control)

    if args.check_only:
        print(json.dumps(readiness_result(args, support), indent=2))
        return

    if (
        not support["supports_gateway_without_protocol_type"]
        or not support["supports_http_agentcore_runtime_target"]
    ):
        print(json.dumps(readiness_result(args, support), indent=2))
        return

    if not args.runtime_arn:
        raise RuntimeError("AGENT_RUNTIME_ARN_IAM, AGENT_RUNTIME_ARN, or --runtime-arn is required")

    account_id = get_account_id(session)
    role_arn = ensure_gateway_role(
        session,
        args.role_name,
        args.runtime_arn,
        account_id,
        args.region,
    )
    gateway = create_runtime_frontdoor_gateway(
        control,
        args.gateway_name,
        role_arn,
        args.authorizer_type,
    )
    ready_gateway = wait_for_gateway_ready(control, gateway["gatewayId"])
    target = create_runtime_target(
        control,
        ready_gateway["gatewayId"],
        args.target_name,
        args.runtime_arn,
        args.qualifier,
    )
    ready_target = wait_for_target_ready(
        control,
        ready_gateway["gatewayId"],
        target["targetId"],
    )

    env_values = {
        "PROMPT4_RUNTIME_FRONTDOOR_IAM_GATEWAY_ID": ready_gateway["gatewayId"],
        "PROMPT4_RUNTIME_FRONTDOOR_IAM_GATEWAY_ARN": ready_gateway["gatewayArn"],
        "PROMPT4_RUNTIME_FRONTDOOR_IAM_GATEWAY_URL": ready_gateway.get("gatewayUrl", ""),
        "PROMPT4_RUNTIME_FRONTDOOR_IAM_TARGET_ID": ready_target["targetId"],
        "PROMPT4_RUNTIME_FRONTDOOR_IAM_ROLE_ARN": role_arn,
    }
    write_env_file(Path(args.env_file), env_values)

    print(json.dumps({"scenario": "gateway_fronted_runtime_iam_sigv4", "status": "created", **env_values}, indent=2))


if __name__ == "__main__":
    main()
