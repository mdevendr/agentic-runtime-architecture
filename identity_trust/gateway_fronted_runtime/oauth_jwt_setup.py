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
    get_account_id,
    local_model_support,
    runtime_target_configuration,
    validate_name,
    wait_for_gateway_ready,
    wait_for_target_ready,
    write_env_file,
)
from caller_iam_setup import ensure_gateway_role


IDENTITY_DIR = Path(__file__).resolve().parent.parent
IDENTITY_PROVIDER_ENV = IDENTITY_DIR / "identity_provider.env"


def load_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip()
    return values


def env_or_file(name: str, values: dict[str, str], default: str | None = None) -> str | None:
    return os.getenv(name) or values.get(name) or default


def find_oauth_provider_by_name(control, name: str) -> dict[str, Any] | None:
    request: dict[str, Any] = {}
    while True:
        response = control.list_oauth2_credential_providers(**request)
        for provider in response.get("credentialProviders", []):
            if provider.get("name") == name:
                return control.get_oauth2_credential_provider(
                    name=name,
                )
        next_token = response.get("nextToken")
        if not next_token:
            return None
        request["nextToken"] = next_token


def ensure_oauth_provider(
    control,
    name: str,
    discovery_url: str,
    client_id: str,
    client_secret: str,
    client_auth_method: str,
) -> dict[str, Any]:
    existing = find_oauth_provider_by_name(control, name)
    if existing:
        print(f"Updating existing OAuth2 credential provider: {existing['credentialProviderArn']}")
        response = control.update_oauth2_credential_provider(
            name=name,
            credentialProviderVendor="CustomOauth2",
            oauth2ProviderConfigInput={
                "customOauth2ProviderConfig": {
                    "oauthDiscovery": {
                        "discoveryUrl": discovery_url,
                    },
                    "clientId": client_id,
                    "clientSecret": client_secret,
                    "clientAuthenticationMethod": client_auth_method,
                }
            },
        )
        redacted = dict(response)
        if "clientSecretArn" in redacted:
            redacted["clientSecretArn"] = {"secretArn": "REDACTED"}
        print(json.dumps(redacted, indent=2, default=str))
        response["credentialProviderArn"] = existing["credentialProviderArn"]
        return response

    response = control.create_oauth2_credential_provider(
        name=name,
        credentialProviderVendor="CustomOauth2",
        oauth2ProviderConfigInput={
            "customOauth2ProviderConfig": {
                "oauthDiscovery": {
                    "discoveryUrl": discovery_url,
                },
                "clientId": client_id,
                "clientSecret": client_secret,
                "clientAuthenticationMethod": client_auth_method,
            }
        },
        tags={
            "ArchitecturePattern": "GatewayFrontedRuntime",
            "TargetAuthMode": "AgentCoreIdentityOAuthJwt",
        },
    )
    print("OAuth2 credential provider create response:")
    redacted = dict(response)
    if "clientSecretArn" in redacted:
        redacted["clientSecretArn"] = {"secretArn": "REDACTED"}
    print(json.dumps(redacted, indent=2, default=str))
    return response


def oauth_credential_provider_configuration(
    provider_arn: str,
    scope: str,
) -> list[dict[str, Any]]:
    return [
        {
            "credentialProviderType": "OAUTH",
            "credentialProvider": {
                "oauthCredentialProvider": {
                    "providerArn": provider_arn,
                    "scopes": [scope],
                    "grantType": "CLIENT_CREDENTIALS",
                }
            },
        }
    ]


def put_oauth_gateway_role_policy(
    session: boto3.Session,
    role_arn: str,
    region: str,
    account_id: str,
    gateway_id: str,
    credential_provider_arn: str,
    secret_arn: str,
) -> None:
    role_name = role_arn.rsplit("/", 1)[-1]
    iam = session.client("iam")
    iam.put_role_policy(
        RoleName=role_name,
        PolicyName="AllowAgentCoreOAuthCredentialProviderAccess",
        PolicyDocument=json.dumps(
            {
                "Version": "2012-10-17",
                "Statement": [
                    {
                        "Sid": "GetWorkloadAccessToken",
                        "Effect": "Allow",
                        "Action": ["bedrock-agentcore:GetWorkloadAccessToken"],
                        "Resource": [
                            f"arn:aws:bedrock-agentcore:{region}:{account_id}:workload-identity-directory/default",
                            f"arn:aws:bedrock-agentcore:{region}:{account_id}:workload-identity-directory/default/workload-identity/{gateway_id}",
                        ],
                    },
                    {
                        "Sid": "GetResourceOauth2Token",
                        "Effect": "Allow",
                        "Action": ["bedrock-agentcore:GetResourceOauth2Token"],
                        "Resource": [
                            f"arn:aws:bedrock-agentcore:{region}:{account_id}:token-vault/default",
                            credential_provider_arn,
                            f"arn:aws:bedrock-agentcore:{region}:{account_id}:workload-identity-directory/default",
                            f"arn:aws:bedrock-agentcore:{region}:{account_id}:workload-identity-directory/default/workload-identity/{gateway_id}",
                        ],
                    },
                    {
                        "Sid": "GetSecretValue",
                        "Effect": "Allow",
                        "Action": ["secretsmanager:GetSecretValue"],
                        "Resource": [secret_arn],
                    },
                ],
            }
        ),
    )
    print(f"Attached OAuth credential access policy to {role_name}")


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
    provider_arn: str,
    scope: str,
) -> dict[str, Any]:
    try:
        response = control.create_gateway_target(
            gatewayIdentifier=gateway_id,
            name=target_name,
            description="AgentCore Runtime target authorized with OAuth/JWT from AgentCore Identity credential provider.",
            targetConfiguration=runtime_target_configuration(runtime_arn, qualifier),
            credentialProviderConfigurations=oauth_credential_provider_configuration(
                provider_arn,
                scope,
            ),
        )
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") != "ConflictException":
            raise
        existing = find_target_by_name(control, gateway_id, target_name)
        if not existing:
            raise RuntimeError(
                f"Gateway target {target_name} already exists but was not returned by ListGatewayTargets."
            ) from exc
        print(f"Using existing OAuth/JWT Runtime target: {existing['targetId']}")
        return existing

    print("OAuth/JWT Runtime target create response:")
    print(json.dumps(response, indent=2, default=str))
    return response


def readiness_result(args: argparse.Namespace, support: dict[str, Any]) -> dict[str, Any]:
    has_api_support = (
        support["supports_gateway_without_protocol_type"]
        and support["supports_http_agentcore_runtime_target"]
    )
    has_runtime_arn = bool(args.runtime_arn)
    has_oauth_inputs = bool(args.discovery_url and args.client_id and args.client_secret and args.scope)
    return {
        "scenario": "gateway_fronted_runtime_oauth_jwt",
        "implemented": has_api_support,
        "ready_for_deploy": has_api_support and has_runtime_arn and has_oauth_inputs,
        "reason": (
            "SDK model supports Gateway HTTP Runtime targets and OAUTH target credentials."
            if has_api_support
            else "Installed boto3/botocore model does not expose Gateway HTTP Runtime targets yet."
        ),
        "required_inputs": {
            "AGENT_RUNTIME_ARN_OAUTH_CLIENT or AGENT_RUNTIME_ARN": has_runtime_arn,
            "AGENTCORE_OAUTH_CLIENT_DISCOVERY_URL": bool(args.discovery_url),
            "PROMPT4_COGNITO_MACHINE_CLIENT_ID": bool(args.client_id),
            "PROMPT4_COGNITO_MACHINE_CLIENT_SECRET": bool(args.client_secret),
            "PROMPT4_COGNITO_SCOPE": bool(args.scope),
        },
        "desired_gateway": {
            "name": args.gateway_name,
            "authorizerType": args.authorizer_type,
            "protocolType": None,
        },
        "desired_target_configuration": runtime_target_configuration(
            args.runtime_arn or "<oauth-runtime-arn>",
            args.qualifier,
        ),
        "desired_credential_provider_configurations": oauth_credential_provider_configuration(
            "<oauth2-credential-provider-arn>",
            args.scope or "<scope>",
        ),
        "notes": [
            "This is the third Gateway -> Runtime target authorization mode.",
            "Gateway authenticates the original caller at the front door.",
            "Gateway obtains an OAuth access token through an AgentCore Identity OAuth2 credential provider.",
            "Gateway calls the Runtime target with Authorization: Bearer <token>.",
            "Runtime JWT authorizer validates the Cognito issuer, signature, client/app claim, audience, and scope.",
        ],
    }


def main() -> None:
    env_values = load_env_file(IDENTITY_PROVIDER_ENV)
    parser = argparse.ArgumentParser(
        description="Create Gateway-fronted Runtime mode 3: AgentCore Identity OAuth/JWT."
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
        default=os.getenv("PROMPT4_RUNTIME_FRONTDOOR_OAUTH_GATEWAY_NAME", "prompt4-runtime-frontdoor-oauth-jwt"),
    )
    parser.add_argument(
        "--target-name",
        default=os.getenv("PROMPT4_RUNTIME_FRONTDOOR_OAUTH_TARGET_NAME", "prompt4RuntimeOauthJwtTarget"),
    )
    parser.add_argument(
        "--role-name",
        default=os.getenv("PROMPT4_RUNTIME_FRONTDOOR_OAUTH_ROLE_NAME", "Prompt4RuntimeFrontdoorOauthJwtRole"),
    )
    parser.add_argument(
        "--provider-name",
        default=os.getenv("PROMPT4_RUNTIME_FRONTDOOR_OAUTH_PROVIDER_NAME", "prompt4-runtime-frontdoor-cognito-oauth"),
    )
    parser.add_argument(
        "--discovery-url",
        default=env_or_file("AGENTCORE_OAUTH_CLIENT_DISCOVERY_URL", env_values),
    )
    parser.add_argument(
        "--client-id",
        default=env_or_file("PROMPT4_COGNITO_MACHINE_CLIENT_ID", env_values),
    )
    parser.add_argument(
        "--client-secret",
        default=env_or_file("PROMPT4_COGNITO_MACHINE_CLIENT_SECRET", env_values),
    )
    parser.add_argument(
        "--scope",
        default=env_or_file("PROMPT4_COGNITO_SCOPE", env_values, "agentcore-runtime/invoke"),
    )
    parser.add_argument(
        "--client-auth-method",
        default=os.getenv("PROMPT4_RUNTIME_FRONTDOOR_OAUTH_CLIENT_AUTH_METHOD", "CLIENT_SECRET_POST"),
        choices=["CLIENT_SECRET_BASIC", "CLIENT_SECRET_POST"],
    )
    parser.add_argument(
        "--authorizer-type",
        default=os.getenv("PROMPT4_RUNTIME_FRONTDOOR_AUTHORIZER", "AWS_IAM"),
        choices=["AWS_IAM", "CUSTOM_JWT"],
    )
    parser.add_argument(
        "--env-file",
        default=str(IDENTITY_DIR / "gateway_fronted_runtime_oauth_jwt.env"),
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
    if not args.discovery_url or not args.client_id or not args.client_secret or not args.scope:
        raise RuntimeError(
            "Cognito OAuth values are required. Run identity_provider_setup.py and keep identity_provider.env available."
        )

    account_id = get_account_id(session)
    role_arn = ensure_gateway_role(session, args.role_name, account_id, args.region)
    provider = ensure_oauth_provider(
        control,
        args.provider_name,
        args.discovery_url,
        args.client_id,
        args.client_secret,
        args.client_auth_method,
    )
    gateway = create_runtime_frontdoor_gateway(
        control,
        args.gateway_name,
        role_arn,
        args.authorizer_type,
    )
    ready_gateway = wait_for_gateway_ready(control, gateway["gatewayId"])
    secret_arn = provider.get("clientSecretArn", {}).get("secretArn")
    if not secret_arn:
        raise RuntimeError("OAuth credential provider did not return clientSecretArn.secretArn")
    put_oauth_gateway_role_policy(
        session,
        role_arn,
        args.region,
        account_id,
        ready_gateway["gatewayId"],
        provider["credentialProviderArn"],
        secret_arn,
    )
    target = create_runtime_target(
        control,
        ready_gateway["gatewayId"],
        args.target_name,
        args.runtime_arn,
        args.qualifier,
        provider["credentialProviderArn"],
        args.scope,
    )
    ready_target = wait_for_target_ready(
        control,
        ready_gateway["gatewayId"],
        target["targetId"],
    )

    output_values = {
        "PROMPT4_RUNTIME_FRONTDOOR_OAUTH_GATEWAY_ID": ready_gateway["gatewayId"],
        "PROMPT4_RUNTIME_FRONTDOOR_OAUTH_GATEWAY_ARN": ready_gateway["gatewayArn"],
        "PROMPT4_RUNTIME_FRONTDOOR_OAUTH_GATEWAY_URL": ready_gateway.get("gatewayUrl", ""),
        "PROMPT4_RUNTIME_FRONTDOOR_OAUTH_TARGET_ID": ready_target["targetId"],
        "PROMPT4_RUNTIME_FRONTDOOR_OAUTH_TARGET_NAME": args.target_name,
        "PROMPT4_RUNTIME_FRONTDOOR_OAUTH_ROLE_ARN": role_arn,
        "PROMPT4_RUNTIME_FRONTDOOR_OAUTH_PROVIDER_ARN": provider["credentialProviderArn"],
    }
    write_env_file(Path(args.env_file), output_values)

    print(json.dumps({"scenario": "gateway_fronted_runtime_oauth_jwt", "status": "created", **output_values}, indent=2))


if __name__ == "__main__":
    main()
