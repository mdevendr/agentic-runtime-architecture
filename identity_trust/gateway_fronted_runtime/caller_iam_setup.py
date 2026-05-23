import argparse
import json
import os
import time
from pathlib import Path
from typing import Any

import boto3
from botocore.exceptions import ClientError

from iam_sigv4_setup import (
    GATEWAY_NAME_PATTERN,
    ROLE_NAME_PATTERN,
    TARGET_NAME_PATTERN,
    create_runtime_frontdoor_gateway,
    find_gateway_by_name,
    get_account_id,
    local_model_support,
    runtime_target_configuration,
    validate_name,
    wait_for_gateway_ready,
    wait_for_target_ready,
    write_env_file,
)


IDENTITY_DIR = Path(__file__).resolve().parent.parent


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


def ensure_gateway_role(
    session: boto3.Session,
    role_name: str,
    account_id: str,
    region: str,
) -> str:
    iam = session.client("iam")
    trust_policy = gateway_role_trust_policy(account_id, region)

    try:
        role = iam.get_role(RoleName=role_name)["Role"]
        print(f"Using existing Gateway front-door caller-IAM role: {role['Arn']}")
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
            Description="AgentCore Gateway role for caller IAM credentials Runtime target.",
            Tags=[{"Key": "ArchitecturePattern", "Value": "GatewayFrontedRuntime"}],
        )["Role"]
        print(f"Created Gateway front-door caller-IAM role: {role['Arn']}")

    iam.get_waiter("role_exists").wait(RoleName=role_name)
    time.sleep(10)
    return role["Arn"]


def caller_iam_credential_provider(region: str) -> list[dict[str, Any]]:
    return [
        {
            "credentialProviderType": "CALLER_IAM_CREDENTIALS",
            "credentialProvider": {
                "iamCredentialProvider": {
                    "service": "bedrock-agentcore",
                    "region": region,
                }
            },
        }
    ]


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


def create_runtime_target(
    control,
    gateway_id: str,
    target_name: str,
    runtime_arn: str,
    qualifier: str | None,
    region: str,
) -> dict[str, Any]:
    try:
        response = control.create_gateway_target(
            gatewayIdentifier=gateway_id,
            name=target_name,
            description="AgentCore Runtime target authorized with caller IAM credentials.",
            targetConfiguration=runtime_target_configuration(runtime_arn, qualifier),
            credentialProviderConfigurations=caller_iam_credential_provider(region),
        )
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") != "ConflictException":
            raise
        existing = find_target_by_name(control, gateway_id, target_name)
        if not existing:
            raise RuntimeError(
                f"Gateway target {target_name} already exists but was not returned by ListGatewayTargets."
            ) from exc
        print(f"Using existing caller-IAM Runtime target: {existing['targetId']}")
        return existing

    print("Caller-IAM Runtime target create response:")
    print(json.dumps(response, indent=2, default=str))
    return response


def runtime_resources(runtime_arn: str) -> list[str]:
    return [
        runtime_arn,
        f"{runtime_arn}/runtime-endpoint/*",
    ]


def put_caller_allow_policy(
    session: boto3.Session,
    role_arn: str,
    gateway_arn: str,
    runtime_arn: str,
) -> None:
    role_name = role_arn.rsplit("/", 1)[-1]
    iam = session.client("iam")
    iam.put_role_policy(
        RoleName=role_name,
        PolicyName="AllowPrompt4GatewayFrontdoorAndRuntime",
        PolicyDocument=json.dumps(
            {
                "Version": "2012-10-17",
                "Statement": [
                    {
                        "Sid": "AllowInvokeGatewayFrontdoor",
                        "Effect": "Allow",
                        "Action": ["bedrock-agentcore:InvokeGateway"],
                        "Resource": [gateway_arn],
                    },
                    {
                        "Sid": "AllowInvokeRuntimeAsCaller",
                        "Effect": "Allow",
                        "Action": ["bedrock-agentcore:InvokeAgentRuntime"],
                        "Resource": runtime_resources(runtime_arn),
                    },
                ],
            }
        ),
    )
    print(f"Attached caller allow policy to {role_name}")


def readiness_result(args: argparse.Namespace, support: dict[str, Any]) -> dict[str, Any]:
    has_api_support = (
        support["supports_gateway_without_protocol_type"]
        and support["supports_http_agentcore_runtime_target"]
    )
    has_runtime_arn = bool(args.runtime_arn)
    return {
        "scenario": "gateway_fronted_runtime_caller_iam",
        "implemented": has_api_support,
        "ready_for_deploy": has_api_support and has_runtime_arn,
        "reason": (
            "SDK model supports Gateway HTTP Runtime targets and CALLER_IAM_CREDENTIALS."
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
        "desired_credential_provider_configurations": caller_iam_credential_provider(args.region),
        "notes": [
            "This is the second Gateway -> Runtime target authorization mode.",
            "Gateway authenticates the original caller at the front door.",
            "Gateway signs the Runtime target request with caller IAM credentials.",
            "Runtime IAM auth evaluates the original caller identity, not the Gateway service role.",
            "The caller must be allowed to invoke both the Gateway and the Runtime.",
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Create Gateway-fronted Runtime mode 2: caller IAM credentials."
    )
    parser.add_argument("--profile", help="AWS profile to use.")
    parser.add_argument("--region", default=os.getenv("AWS_REGION", "eu-west-2"))
    parser.add_argument(
        "--runtime-arn",
        default=os.getenv("AGENT_RUNTIME_ARN_IAM") or os.getenv("AGENT_RUNTIME_ARN"),
    )
    parser.add_argument("--qualifier", default=os.getenv("PROMPT4_RUNTIME_TARGET_QUALIFIER"))
    parser.add_argument(
        "--gateway-name",
        default=os.getenv("PROMPT4_RUNTIME_FRONTDOOR_CALLER_IAM_GATEWAY_NAME", "prompt4-runtime-frontdoor-caller-iam"),
    )
    parser.add_argument(
        "--target-name",
        default=os.getenv("PROMPT4_RUNTIME_FRONTDOOR_CALLER_IAM_TARGET_NAME", "prompt4RuntimeCallerIamTarget"),
    )
    parser.add_argument(
        "--role-name",
        default=os.getenv("PROMPT4_RUNTIME_FRONTDOOR_CALLER_IAM_ROLE_NAME", "Prompt4RuntimeFrontdoorCallerIamRole"),
    )
    parser.add_argument(
        "--authorizer-type",
        default=os.getenv("PROMPT4_RUNTIME_FRONTDOOR_AUTHORIZER", "AWS_IAM"),
        choices=["AWS_IAM", "CUSTOM_JWT"],
    )
    parser.add_argument(
        "--caller-allow-role-arn",
        default=os.getenv("PROMPT4_CLIENT_RUNTIME_IAM_ROLE_ALLOW_ARN"),
        help="Optional caller role to update with InvokeGateway and InvokeAgentRuntime permissions.",
    )
    parser.add_argument(
        "--env-file",
        default=str(IDENTITY_DIR / "gateway_fronted_runtime_caller_iam.env"),
    )
    parser.add_argument("--check-only", action="store_true")
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
    role_arn = ensure_gateway_role(session, args.role_name, account_id, args.region)
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
        args.region,
    )
    ready_target = wait_for_target_ready(
        control,
        ready_gateway["gatewayId"],
        target["targetId"],
    )

    if args.caller_allow_role_arn:
        put_caller_allow_policy(
            session,
            args.caller_allow_role_arn,
            ready_gateway["gatewayArn"],
            args.runtime_arn,
        )

    env_values = {
        "PROMPT4_RUNTIME_FRONTDOOR_CALLER_IAM_GATEWAY_ID": ready_gateway["gatewayId"],
        "PROMPT4_RUNTIME_FRONTDOOR_CALLER_IAM_GATEWAY_ARN": ready_gateway["gatewayArn"],
        "PROMPT4_RUNTIME_FRONTDOOR_CALLER_IAM_GATEWAY_URL": ready_gateway.get("gatewayUrl", ""),
        "PROMPT4_RUNTIME_FRONTDOOR_CALLER_IAM_TARGET_ID": ready_target["targetId"],
        "PROMPT4_RUNTIME_FRONTDOOR_CALLER_IAM_ROLE_ARN": role_arn,
        "PROMPT4_RUNTIME_FRONTDOOR_CALLER_IAM_TARGET_NAME": args.target_name,
    }
    write_env_file(Path(args.env_file), env_values)

    print(json.dumps({"scenario": "gateway_fronted_runtime_caller_iam", "status": "created", **env_values}, indent=2))


if __name__ == "__main__":
    main()

