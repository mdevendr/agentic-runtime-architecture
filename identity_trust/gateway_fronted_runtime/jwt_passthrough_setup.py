import argparse
import json
import os
from pathlib import Path
from typing import Any

import boto3
from botocore.exceptions import ClientError

from iam_sigv4_setup import (
    GATEWAY_NAME_PATTERN,
    ROLE_NAME_PATTERN,
    TARGET_NAME_PATTERN,
    create_runtime_frontdoor_gateway,
    get_account_id,
    local_model_support,
    runtime_target_configuration,
    validate_name,
    wait_for_gateway_ready,
    wait_for_target_ready,
    write_env_file,
)
from caller_iam_setup import ensure_gateway_role
from oauth_jwt_setup import load_env_file, env_or_file


IDENTITY_DIR = Path(__file__).resolve().parent.parent
IDENTITY_PROVIDER_ENV = IDENTITY_DIR / "identity_provider.env"


def jwt_passthrough_configuration() -> list[dict[str, str]]:
    return [{"credentialProviderType": "JWT_PASSTHROUGH"}]


def custom_jwt_authorizer(
    discovery_url: str,
    allowed_clients: list[str],
    allowed_audiences: list[str],
    allowed_scopes: list[str],
) -> dict[str, Any]:
    authorizer: dict[str, Any] = {
        "customJWTAuthorizer": {
            "discoveryUrl": discovery_url,
        }
    }
    if allowed_clients:
        authorizer["customJWTAuthorizer"]["allowedClients"] = allowed_clients
    if allowed_audiences:
        authorizer["customJWTAuthorizer"]["allowedAudience"] = allowed_audiences
    if allowed_scopes:
        authorizer["customJWTAuthorizer"]["allowedScopes"] = allowed_scopes
    return authorizer


def split_csv(value: str | None) -> list[str]:
    if not value:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


def create_jwt_gateway(
    control,
    gateway_name: str,
    role_arn: str,
    authorizer_configuration: dict[str, Any],
) -> dict[str, Any]:
    try:
        response = control.create_gateway(
            name=gateway_name,
            description="Prompt 4 Gateway-fronted Runtime using inbound JWT passthrough.",
            roleArn=role_arn,
            authorizerType="CUSTOM_JWT",
            authorizerConfiguration=authorizer_configuration,
            exceptionLevel="DEBUG",
            tags={
                "ArchitecturePattern": "GatewayFrontedRuntime",
                "TargetAuthMode": "JwtPassthrough",
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
        print(f"Using existing JWT passthrough Gateway: {existing['gatewayArn']}")
        return existing

    print("JWT passthrough Gateway create response:")
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
) -> dict[str, Any]:
    try:
        response = control.create_gateway_target(
            gatewayIdentifier=gateway_id,
            name=target_name,
            description="AgentCore Runtime target authorized with inbound JWT token passthrough.",
            targetConfiguration=runtime_target_configuration(runtime_arn, qualifier),
            credentialProviderConfigurations=jwt_passthrough_configuration(),
        )
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") != "ConflictException":
            raise
        existing = find_target_by_name(control, gateway_id, target_name)
        if not existing:
            raise RuntimeError(
                f"Gateway target {target_name} already exists but was not returned by ListGatewayTargets."
            ) from exc
        print(f"Using existing JWT passthrough Runtime target: {existing['targetId']}")
        return existing

    print("JWT passthrough Runtime target create response:")
    print(json.dumps(response, indent=2, default=str))
    return response


def readiness_result(args: argparse.Namespace, support: dict[str, Any]) -> dict[str, Any]:
    has_api_support = (
        support["supports_gateway_without_protocol_type"]
        and support["supports_http_agentcore_runtime_target"]
    )
    has_runtime_arn = bool(args.runtime_arn)
    has_jwt_config = bool(args.discovery_url and args.allowed_clients and args.allowed_scopes)
    return {
        "scenario": "gateway_fronted_runtime_jwt_passthrough",
        "implemented": has_api_support,
        "ready_for_deploy": has_api_support and has_runtime_arn and has_jwt_config,
        "reason": (
            "SDK model supports Gateway HTTP Runtime targets and JWT_PASSTHROUGH."
            if has_api_support
            else "Installed boto3/botocore model does not expose Gateway HTTP Runtime targets yet."
        ),
        "required_inputs": {
            "AGENT_RUNTIME_ARN_OAUTH_CLIENT or AGENT_RUNTIME_ARN": has_runtime_arn,
            "AGENTCORE_OAUTH_CLIENT_DISCOVERY_URL": bool(args.discovery_url),
            "AGENTCORE_OAUTH_CLIENT_ALLOWED_CLIENTS": bool(args.allowed_clients),
            "AGENTCORE_OAUTH_CLIENT_ALLOWED_SCOPES": bool(args.allowed_scopes),
        },
        "desired_gateway": {
            "name": args.gateway_name,
            "authorizerType": "CUSTOM_JWT",
            "protocolType": None,
        },
        "desired_target_configuration": runtime_target_configuration(
            args.runtime_arn or "<oauth-runtime-arn>",
            args.qualifier,
        ),
        "desired_credential_provider_configurations": jwt_passthrough_configuration(),
        "notes": [
            "This is the fourth Gateway -> Runtime target authorization mode.",
            "Gateway validates the inbound JWT with CUSTOM_JWT authorizer.",
            "Gateway passes the same Authorization bearer token through to Runtime.",
            "Runtime JWT authorizer performs final token authorization.",
            "Gateway does not fetch a token from AgentCore Identity in this mode.",
        ],
    }


def main() -> None:
    env_values = load_env_file(IDENTITY_PROVIDER_ENV)
    parser = argparse.ArgumentParser(
        description="Create Gateway-fronted Runtime mode 4: JWT token passthrough."
    )
    parser.add_argument("--profile", help="AWS profile to use.")
    parser.add_argument("--region", default=os.getenv("AWS_REGION", "eu-west-2"))
    parser.add_argument(
        "--runtime-arn",
        default=os.getenv("AGENT_RUNTIME_ARN_OAUTH_CLIENT") or os.getenv("AGENT_RUNTIME_ARN"),
    )
    parser.add_argument("--qualifier", default=os.getenv("PROMPT4_RUNTIME_TARGET_QUALIFIER"))
    parser.add_argument(
        "--gateway-name",
        default=os.getenv("PROMPT4_RUNTIME_FRONTDOOR_JWT_GATEWAY_NAME", "prompt4-runtime-frontdoor-jwt-pass"),
    )
    parser.add_argument(
        "--target-name",
        default=os.getenv("PROMPT4_RUNTIME_FRONTDOOR_JWT_TARGET_NAME", "prompt4RuntimeJwtPassthroughTarget"),
    )
    parser.add_argument(
        "--role-name",
        default=os.getenv("PROMPT4_RUNTIME_FRONTDOOR_JWT_ROLE_NAME", "Prompt4RuntimeFrontdoorJwtPassRole"),
    )
    parser.add_argument(
        "--discovery-url",
        default=env_or_file("AGENTCORE_OAUTH_CLIENT_DISCOVERY_URL", env_values),
    )
    parser.add_argument(
        "--allowed-clients",
        default=env_or_file("AGENTCORE_OAUTH_CLIENT_ALLOWED_CLIENTS", env_values),
    )
    parser.add_argument(
        "--allowed-audiences",
        default=env_or_file("AGENTCORE_OAUTH_CLIENT_ALLOWED_AUDIENCES", env_values),
    )
    parser.add_argument(
        "--allowed-scopes",
        default=env_or_file("AGENTCORE_OAUTH_CLIENT_ALLOWED_SCOPES", env_values),
    )
    parser.add_argument(
        "--env-file",
        default=str(IDENTITY_DIR / "gateway_fronted_runtime_jwt_passthrough.env"),
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
        raise RuntimeError("AGENT_RUNTIME_ARN_OAUTH_CLIENT, AGENT_RUNTIME_ARN, or --runtime-arn is required")
    if not args.discovery_url or not args.allowed_clients or not args.allowed_scopes:
        raise RuntimeError(
            "JWT authorizer values are required. Run identity_provider_setup.py and keep identity_provider.env available."
        )

    account_id = get_account_id(session)
    role_arn = ensure_gateway_role(session, args.role_name, account_id, args.region)
    gateway = create_jwt_gateway(
        control,
        args.gateway_name,
        role_arn,
        custom_jwt_authorizer(
            args.discovery_url,
            split_csv(args.allowed_clients),
            split_csv(args.allowed_audiences),
            split_csv(args.allowed_scopes),
        ),
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

    output_values = {
        "PROMPT4_RUNTIME_FRONTDOOR_JWT_GATEWAY_ID": ready_gateway["gatewayId"],
        "PROMPT4_RUNTIME_FRONTDOOR_JWT_GATEWAY_ARN": ready_gateway["gatewayArn"],
        "PROMPT4_RUNTIME_FRONTDOOR_JWT_GATEWAY_URL": ready_gateway.get("gatewayUrl", ""),
        "PROMPT4_RUNTIME_FRONTDOOR_JWT_TARGET_ID": ready_target["targetId"],
        "PROMPT4_RUNTIME_FRONTDOOR_JWT_TARGET_NAME": args.target_name,
        "PROMPT4_RUNTIME_FRONTDOOR_JWT_ROLE_ARN": role_arn,
    }
    write_env_file(Path(args.env_file), output_values)

    print(json.dumps({"scenario": "gateway_fronted_runtime_jwt_passthrough", "status": "created", **output_values}, indent=2))


if __name__ == "__main__":
    main()

