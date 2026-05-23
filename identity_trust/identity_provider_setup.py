import argparse
import json
import os
import re
import secrets
import string
from pathlib import Path
from typing import Any

import boto3
from botocore.exceptions import ClientError


BASE_DIR = Path(__file__).parent
DEFAULT_USER_POOL_NAME = "prompt4-client-runtime-idp"
DEFAULT_RESOURCE_SERVER_ID = "agentcore-runtime"
DEFAULT_SCOPE_NAME = "invoke"
DEFAULT_USER_CLIENT_NAME = "prompt4-user-client"
DEFAULT_MACHINE_CLIENT_NAME = "prompt4-machine-client"
DOMAIN_PATTERN = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")


def get_account_id() -> str:
    return boto3.client("sts").get_caller_identity()["Account"]


def random_password(length: int = 20) -> str:
    alphabet = string.ascii_letters + string.digits + "!@#$%^&*()-_=+"
    while True:
        value = "".join(secrets.choice(alphabet) for _ in range(length))
        if (
            any(char.islower() for char in value)
            and any(char.isupper() for char in value)
            and any(char.isdigit() for char in value)
            and any(char in "!@#$%^&*()-_=+" for char in value)
        ):
            return value


def password_conforms(password: str) -> bool:
    return (
        len(password) >= 12
        and any(char.islower() for char in password)
        and any(char.isupper() for char in password)
        and any(char.isdigit() for char in password)
        and any(char in "!@#$%^&*()-_=+" for char in password)
    )


def find_user_pool(cognito, name: str) -> str | None:
    paginator = cognito.get_paginator("list_user_pools")
    for page in paginator.paginate(MaxResults=60):
        for pool in page.get("UserPools", []):
            if pool.get("Name") == name:
                return pool["Id"]
    return None


def ensure_user_pool(cognito, name: str) -> str:
    existing = find_user_pool(cognito, name)
    if existing:
        print(f"Using existing Cognito user pool: {existing}")
        return existing

    response = cognito.create_user_pool(
        PoolName=name,
        Policies={
            "PasswordPolicy": {
                "MinimumLength": 12,
                "RequireUppercase": True,
                "RequireLowercase": True,
                "RequireNumbers": True,
                "RequireSymbols": True,
            }
        },
        UsernameAttributes=["email"],
        AutoVerifiedAttributes=["email"],
        AdminCreateUserConfig={"AllowAdminCreateUserOnly": True},
    )
    user_pool_id = response["UserPool"]["Id"]
    print(f"Created Cognito user pool: {user_pool_id}")
    return user_pool_id


def ensure_resource_server(
    cognito,
    user_pool_id: str,
    identifier: str,
    scope_name: str,
) -> str:
    scope = {
        "ScopeName": scope_name,
        "ScopeDescription": "Invoke AgentCore Runtime for Prompt 4 identity tests.",
    }
    try:
        cognito.create_resource_server(
            UserPoolId=user_pool_id,
            Identifier=identifier,
            Name="Prompt4 AgentCore Runtime",
            Scopes=[scope],
        )
        print(f"Created Cognito resource server: {identifier}")
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") != "InvalidParameterException":
            raise
        cognito.update_resource_server(
            UserPoolId=user_pool_id,
            Identifier=identifier,
            Name="Prompt4 AgentCore Runtime",
            Scopes=[scope],
        )
        print(f"Updated Cognito resource server: {identifier}")

    return f"{identifier}/{scope_name}"


def ensure_google_provider(
    cognito,
    user_pool_id: str,
    client_id: str | None,
    client_secret: str | None,
) -> bool:
    if not client_id and not client_secret:
        print("Google IdP not configured: PROMPT4_GOOGLE_CLIENT_ID/SECRET not set")
        return False
    if not client_id or not client_secret:
        raise RuntimeError(
            "Both PROMPT4_GOOGLE_CLIENT_ID and PROMPT4_GOOGLE_CLIENT_SECRET are required "
            "to configure Google as a Cognito identity provider."
        )

    provider_details = {
        "client_id": client_id,
        "client_secret": client_secret,
        "authorize_scopes": "openid email profile",
    }
    attribute_mapping = {
        "email": "email",
        "given_name": "given_name",
        "family_name": "family_name",
        "name": "name",
    }

    try:
        cognito.create_identity_provider(
            UserPoolId=user_pool_id,
            ProviderName="Google",
            ProviderType="Google",
            ProviderDetails=provider_details,
            AttributeMapping=attribute_mapping,
        )
        print("Created Cognito Google identity provider")
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") != "DuplicateProviderException":
            raise
        cognito.update_identity_provider(
            UserPoolId=user_pool_id,
            ProviderName="Google",
            ProviderDetails=provider_details,
            AttributeMapping=attribute_mapping,
        )
        print("Updated Cognito Google identity provider")

    return True


def find_user_pool_client(cognito, user_pool_id: str, name: str) -> str | None:
    paginator = cognito.get_paginator("list_user_pool_clients")
    for page in paginator.paginate(UserPoolId=user_pool_id, MaxResults=60):
        for client in page.get("UserPoolClients", []):
            if client.get("ClientName") == name:
                return client["ClientId"]
    return None


def ensure_user_client(
    cognito,
    user_pool_id: str,
    name: str,
    callback_url: str,
    logout_url: str,
    custom_scope: str,
    supported_identity_providers: list[str],
) -> str:
    existing = find_user_pool_client(cognito, user_pool_id, name)
    kwargs = {
        "UserPoolId": user_pool_id,
        "ClientName": name,
        "GenerateSecret": False,
        "ExplicitAuthFlows": [
            "ALLOW_USER_PASSWORD_AUTH",
            "ALLOW_REFRESH_TOKEN_AUTH",
        ],
        "SupportedIdentityProviders": supported_identity_providers,
        "CallbackURLs": [callback_url],
        "LogoutURLs": [logout_url],
        "AllowedOAuthFlows": ["code"],
        "AllowedOAuthScopes": ["openid", "email", "profile", custom_scope],
        "AllowedOAuthFlowsUserPoolClient": True,
        "PreventUserExistenceErrors": "ENABLED",
    }
    if existing:
        update_kwargs = dict(kwargs)
        update_kwargs["ClientId"] = existing
        update_kwargs.pop("GenerateSecret", None)
        cognito.update_user_pool_client(**update_kwargs)
        print(f"Updated OIDC user app client: {existing}")
        return existing

    response = cognito.create_user_pool_client(**kwargs)
    client_id = response["UserPoolClient"]["ClientId"]
    print(f"Created OIDC user app client: {client_id}")
    return client_id


def ensure_machine_client(
    cognito,
    user_pool_id: str,
    name: str,
    custom_scope: str,
) -> tuple[str, str | None]:
    existing = find_user_pool_client(cognito, user_pool_id, name)
    kwargs = {
        "UserPoolId": user_pool_id,
        "ClientName": name,
        "GenerateSecret": True,
        "SupportedIdentityProviders": ["COGNITO"],
        "AllowedOAuthFlows": ["client_credentials"],
        "AllowedOAuthScopes": [custom_scope],
        "AllowedOAuthFlowsUserPoolClient": True,
        "PreventUserExistenceErrors": "ENABLED",
    }
    if existing:
        update_kwargs = dict(kwargs)
        update_kwargs["ClientId"] = existing
        update_kwargs.pop("GenerateSecret", None)
        cognito.update_user_pool_client(**update_kwargs)
        described = cognito.describe_user_pool_client(
            UserPoolId=user_pool_id,
            ClientId=existing,
        )["UserPoolClient"]
        print(f"Updated OAuth machine app client: {existing}")
        return existing, described.get("ClientSecret")

    response = cognito.create_user_pool_client(**kwargs)
    client = response["UserPoolClient"]
    print(f"Created OAuth machine app client: {client['ClientId']}")
    return client["ClientId"], client.get("ClientSecret")


def ensure_domain(cognito, user_pool_id: str, domain_prefix: str) -> str:
    if not DOMAIN_PATTERN.fullmatch(domain_prefix):
        raise RuntimeError(
            "Cognito domain prefix must be lowercase letters, numbers, and hyphens; "
            "it must start and end with a letter or number."
        )

    try:
        response = cognito.describe_user_pool_domain(Domain=domain_prefix)
        if response.get("DomainDescription", {}).get("UserPoolId") == user_pool_id:
            print(f"Using existing Cognito domain prefix: {domain_prefix}")
            return domain_prefix
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") != "ResourceNotFoundException":
            raise

    cognito.create_user_pool_domain(
        Domain=domain_prefix,
        UserPoolId=user_pool_id,
    )
    print(f"Created Cognito domain prefix: {domain_prefix}")
    return domain_prefix


def ensure_test_user(
    cognito,
    user_pool_id: str,
    username: str,
    email: str,
    password: str,
) -> None:
    try:
        cognito.admin_create_user(
            UserPoolId=user_pool_id,
            Username=email,
            UserAttributes=[
                {"Name": "email", "Value": email},
                {"Name": "email_verified", "Value": "true"},
            ],
            MessageAction="SUPPRESS",
        )
        print(f"Created Cognito test user: {email}")
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") != "UsernameExistsException":
            raise
        print(f"Using existing Cognito test user: {email}")

    cognito.admin_set_user_password(
        UserPoolId=user_pool_id,
        Username=email,
        Password=password,
        Permanent=True,
    )
    print(f"Set permanent password for Cognito test user alias: {username}")


def write_env_file(path: Path, values: dict[str, str]) -> None:
    path.write_text(
        "\n".join(f"{key}={value}" for key, value in values.items()) + "\n",
        encoding="utf-8",
    )


def printable_env_value(key: str, value: str, secret_file: str) -> str:
    sensitive_markers = ("SECRET", "TOKEN", "PASSWORD", "ACCESS_KEY", "AUTHORIZATION")
    if any(marker in key for marker in sensitive_markers):
        return f"REDACTED_STORED_IN_{Path(secret_file).name}"
    return value


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Create Cognito identity provider resources for Prompt 4 Runtime JWT/OAuth tests."
    )
    parser.add_argument("--region", default=os.getenv("AWS_REGION", "eu-west-2"))
    parser.add_argument("--user-pool-name", default=os.getenv("PROMPT4_COGNITO_USER_POOL_NAME", DEFAULT_USER_POOL_NAME))
    parser.add_argument("--resource-server-id", default=os.getenv("PROMPT4_COGNITO_RESOURCE_SERVER_ID", DEFAULT_RESOURCE_SERVER_ID))
    parser.add_argument("--scope-name", default=os.getenv("PROMPT4_COGNITO_SCOPE_NAME", DEFAULT_SCOPE_NAME))
    parser.add_argument("--user-client-name", default=os.getenv("PROMPT4_COGNITO_USER_CLIENT_NAME", DEFAULT_USER_CLIENT_NAME))
    parser.add_argument("--machine-client-name", default=os.getenv("PROMPT4_COGNITO_MACHINE_CLIENT_NAME", DEFAULT_MACHINE_CLIENT_NAME))
    parser.add_argument("--callback-url", default=os.getenv("PROMPT4_COGNITO_CALLBACK_URL", "http://localhost:8080/callback"))
    parser.add_argument("--logout-url", default=os.getenv("PROMPT4_COGNITO_LOGOUT_URL", "http://localhost:8080/logout"))
    parser.add_argument("--test-username", default=os.getenv("PROMPT4_COGNITO_TEST_USERNAME", "prompt4-test-user"))
    parser.add_argument("--test-user-email", default=os.getenv("PROMPT4_COGNITO_TEST_USER_EMAIL", "prompt4-test-user@example.com"))
    parser.add_argument("--test-user-password", default=os.getenv("PROMPT4_COGNITO_TEST_USER_PASSWORD"))
    parser.add_argument("--skip-test-user", action="store_true", help="Do not create/reset a native Cognito test user.")
    parser.add_argument("--domain-prefix", default=os.getenv("PROMPT4_COGNITO_DOMAIN_PREFIX"))
    parser.add_argument(
        "--env-file",
        default=str(BASE_DIR / "identity_provider.env"),
        help="Write dotenv-style output values to this path.",
    )
    args = parser.parse_args()

    region = args.region
    account_id = get_account_id()
    domain_prefix = args.domain_prefix or f"prompt4-agentcore-{account_id}-{region}".lower()
    password = args.test_user_password or random_password()
    if args.test_user_password and not password_conforms(args.test_user_password):
        raise RuntimeError(
            "PROMPT4_COGNITO_TEST_USER_PASSWORD must be at least 12 characters and include "
            "lowercase, uppercase, number, and symbol characters. Unset it to let the script "
            "generate a compliant password."
        )

    cognito = boto3.client("cognito-idp", region_name=region)

    user_pool_id = ensure_user_pool(cognito, args.user_pool_name)
    scope = ensure_resource_server(
        cognito,
        user_pool_id,
        args.resource_server_id,
        args.scope_name,
    )
    google_enabled = ensure_google_provider(
        cognito,
        user_pool_id,
        os.getenv("PROMPT4_GOOGLE_CLIENT_ID"),
        os.getenv("PROMPT4_GOOGLE_CLIENT_SECRET"),
    )
    user_identity_providers = ["COGNITO"]
    if google_enabled:
        user_identity_providers.append("Google")

    user_client_id = ensure_user_client(
        cognito,
        user_pool_id,
        args.user_client_name,
        args.callback_url,
        args.logout_url,
        scope,
        user_identity_providers,
    )
    machine_client_id, machine_client_secret = ensure_machine_client(
        cognito,
        user_pool_id,
        args.machine_client_name,
        scope,
    )
    ensure_domain(cognito, user_pool_id, domain_prefix)
    if not args.skip_test_user:
        ensure_test_user(
            cognito,
            user_pool_id,
            args.test_username,
            args.test_user_email,
            password,
        )

    discovery_url = (
        f"https://cognito-idp.{region}.amazonaws.com/{user_pool_id}/"
        ".well-known/openid-configuration"
    )
    hosted_ui_base_url = f"https://{domain_prefix}.auth.{region}.amazoncognito.com"
    token_url = f"{hosted_ui_base_url}/oauth2/token"

    values = {
        "PROMPT4_COGNITO_USER_POOL_ID": user_pool_id,
        "PROMPT4_COGNITO_DOMAIN_PREFIX": domain_prefix,
        "PROMPT4_COGNITO_HOSTED_UI_BASE_URL": hosted_ui_base_url,
        "PROMPT4_COGNITO_TOKEN_URL": token_url,
        "PROMPT4_COGNITO_USER_CLIENT_ID": user_client_id,
        "PROMPT4_COGNITO_MACHINE_CLIENT_ID": machine_client_id,
        "PROMPT4_COGNITO_SCOPE": scope,
        "AGENTCORE_OIDC_JWT_DISCOVERY_URL": discovery_url,
        "AGENTCORE_OIDC_JWT_ALLOWED_CLIENTS": user_client_id,
        "AGENTCORE_OIDC_JWT_ALLOWED_SCOPES": scope,
        "AGENTCORE_OAUTH_CLIENT_DISCOVERY_URL": discovery_url,
        "AGENTCORE_OAUTH_CLIENT_ALLOWED_CLIENTS": machine_client_id,
        "AGENTCORE_OAUTH_CLIENT_ALLOWED_SCOPES": scope,
    }
    if google_enabled:
        values["PROMPT4_COGNITO_GOOGLE_PROVIDER"] = "Google"
    if not args.skip_test_user:
        values.update(
            {
                "PROMPT4_COGNITO_TEST_USERNAME": args.test_username,
                "PROMPT4_COGNITO_TEST_USER_EMAIL": args.test_user_email,
                "PROMPT4_COGNITO_TEST_USER_PASSWORD": password,
            }
        )
    if machine_client_secret:
        values["PROMPT4_COGNITO_MACHINE_CLIENT_SECRET"] = machine_client_secret

    write_env_file(Path(args.env_file), values)

    print("Cognito identity provider values:")
    for key, value in values.items():
        print(f"export {key}={printable_env_value(key, value, args.env_file)}")


if __name__ == "__main__":
    main()
