import argparse
import json
import os
import re
import time
from pathlib import Path
from typing import Any

import boto3
from botocore.exceptions import ClientError


BASE_DIR = Path(__file__).parent
ROLE_NAME_PATTERN = re.compile(r"^[A-Za-z0-9+=,.@_-]{1,64}$")
USER_NAME_PATTERN = re.compile(r"^[A-Za-z0-9+=,.@_-]{1,64}$")


def require_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"{name} is required")
    return value


def validate_role_name(name: str) -> str:
    if not ROLE_NAME_PATTERN.fullmatch(name):
        raise RuntimeError(f"Invalid IAM role name: {name}")
    return name


def validate_user_name(name: str) -> str:
    if not USER_NAME_PATTERN.fullmatch(name):
        raise RuntimeError(f"Invalid IAM user name: {name}")
    return name


def get_account_id(session: boto3.Session) -> str:
    return session.client("sts").get_caller_identity()["Account"]


def trust_policy(account_id: str) -> dict[str, Any]:
    return {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Principal": {"AWS": f"arn:aws:iam::{account_id}:root"},
                "Action": "sts:AssumeRole",
            }
        ],
    }


def ensure_role(iam, role_name: str, account_id: str, description: str) -> str:
    policy = trust_policy(account_id)
    try:
        role = iam.get_role(RoleName=role_name)["Role"]
        iam.update_assume_role_policy(
            RoleName=role_name,
            PolicyDocument=json.dumps(policy),
        )
        print(f"Using existing identity test role: {role['Arn']}")
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") != "NoSuchEntity":
            raise

        role = iam.create_role(
            RoleName=role_name,
            AssumeRolePolicyDocument=json.dumps(policy),
            Description=description,
            Tags=[{"Key": "ArchitecturePattern", "Value": "Prompt4IdentityTrust"}],
        )["Role"]
        print(f"Created identity test role: {role['Arn']}")

    iam.get_waiter("role_exists").wait(RoleName=role_name)
    return role["Arn"]


def ensure_user(iam, user_name: str) -> str:
    try:
        user = iam.get_user(UserName=user_name)["User"]
        print(f"Using existing identity test user: {user['Arn']}")
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") != "NoSuchEntity":
            raise

        user = iam.create_user(
            UserName=user_name,
            Tags=[{"Key": "ArchitecturePattern", "Value": "Prompt4IdentityTrust"}],
        )["User"]
        print(f"Created identity test user: {user['Arn']}")

    iam.get_waiter("user_exists").wait(UserName=user_name)
    return user["Arn"]


def put_allow_policy(iam, role_name: str, runtime_arn: str) -> None:
    iam.put_role_policy(
        RoleName=role_name,
        PolicyName="AllowInvokePrompt4Runtime",
        PolicyDocument=json.dumps(
            {
                "Version": "2012-10-17",
                "Statement": [
                    {
                        "Effect": "Allow",
                        "Action": ["bedrock-agentcore:InvokeAgentRuntime"],
                        "Resource": runtime_resources(runtime_arn),
                    }
                ],
            }
        ),
    )


def put_explicit_deny_policy(iam, role_name: str, runtime_arn: str) -> None:
    iam.put_role_policy(
        RoleName=role_name,
        PolicyName="DenyInvokePrompt4Runtime",
        PolicyDocument=json.dumps(
            {
                "Version": "2012-10-17",
                "Statement": [
                    {
                        "Effect": "Deny",
                        "Action": ["bedrock-agentcore:InvokeAgentRuntime"],
                        "Resource": runtime_resources(runtime_arn),
                    }
                ],
            }
        ),
    )


def put_user_allow_policy(iam, user_name: str, runtime_arn: str) -> None:
    iam.put_user_policy(
        UserName=user_name,
        PolicyName="AllowInvokePrompt4Runtime",
        PolicyDocument=json.dumps(
            {
                "Version": "2012-10-17",
                "Statement": [
                    {
                        "Effect": "Allow",
                        "Action": ["bedrock-agentcore:InvokeAgentRuntime"],
                        "Resource": runtime_resources(runtime_arn),
                    }
                ],
            }
        ),
    )


def put_user_explicit_deny_policy(iam, user_name: str, runtime_arn: str) -> None:
    iam.put_user_policy(
        UserName=user_name,
        PolicyName="DenyInvokePrompt4Runtime",
        PolicyDocument=json.dumps(
            {
                "Version": "2012-10-17",
                "Statement": [
                    {
                        "Effect": "Deny",
                        "Action": ["bedrock-agentcore:InvokeAgentRuntime"],
                        "Resource": runtime_resources(runtime_arn),
                    }
                ],
            }
        ),
    )


def ensure_user_access_key(iam, user_name: str, rotate: bool) -> dict[str, str]:
    existing_keys = iam.list_access_keys(UserName=user_name)["AccessKeyMetadata"]
    if rotate:
        for key in existing_keys:
            iam.delete_access_key(UserName=user_name, AccessKeyId=key["AccessKeyId"])
        existing_keys = []

    if existing_keys:
        raise RuntimeError(
            f"IAM user {user_name} already has access keys. AWS does not return "
            "existing secret access keys. Re-run with --rotate-iam-user-keys to "
            "replace the dedicated test keys."
        )

    key = iam.create_access_key(UserName=user_name)["AccessKey"]
    return {
        "AccessKeyId": key["AccessKeyId"],
        "SecretAccessKey": key["SecretAccessKey"],
    }


def runtime_resources(runtime_arn: str) -> list[str]:
    return [
        runtime_arn,
        f"{runtime_arn}/runtime-endpoint/*",
    ]


def write_env_file(path: Path, values: dict[str, str]) -> None:
    lines = [f"{key}={value}" for key, value in values.items()]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def printable_env_value(key: str, value: str, secret_file: str) -> str:
    sensitive_markers = ("SECRET", "TOKEN", "PASSWORD", "ACCESS_KEY_ID")
    if any(marker in key for marker in sensitive_markers):
        return f"REDACTED_STORED_IN_{Path(secret_file).name}"
    return value


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Create named IAM role and IAM user identities for Prompt 4 identity tests."
    )
    parser.add_argument("--runtime-arn", default=os.getenv("AGENT_RUNTIME_ARN"))
    parser.add_argument("--profile", help="AWS profile to use for IAM setup.")
    parser.add_argument(
        "--allow-role-name",
        default=os.getenv(
            "PROMPT4_CLIENT_RUNTIME_IAM_ROLE_ALLOW_NAME",
            "Prompt4ClientRuntimeIamRoleAllow",
        ),
    )
    parser.add_argument(
        "--deny-role-name",
        default=os.getenv(
            "PROMPT4_CLIENT_RUNTIME_IAM_ROLE_DENY_NAME",
            "Prompt4ClientRuntimeIamRoleDeny",
        ),
    )
    parser.add_argument(
        "--include-iam-users",
        action="store_true",
        help="Also create IAM user identities and access keys for scenario 2.",
    )
    parser.add_argument(
        "--rotate-iam-user-keys",
        action="store_true",
        help="Delete and recreate access keys for the dedicated scenario 2 IAM users.",
    )
    parser.add_argument(
        "--allow-user-name",
        default=os.getenv(
            "PROMPT4_CLIENT_RUNTIME_IAM_USER_ALLOW_NAME",
            "Prompt4ClientRuntimeIamUserAllow",
        ),
    )
    parser.add_argument(
        "--deny-user-name",
        default=os.getenv(
            "PROMPT4_CLIENT_RUNTIME_IAM_USER_DENY_NAME",
            "Prompt4ClientRuntimeIamUserDeny",
        ),
    )
    parser.add_argument(
        "--env-file",
        default=str(BASE_DIR / "identity_test_roles.env"),
        help="Write role ARNs to this dotenv-style file.",
    )
    parser.add_argument(
        "--user-env-file",
        default=str(BASE_DIR / "identity_test_users.env"),
        help="Write IAM user ARNs and access keys to this dotenv-style file.",
    )
    args = parser.parse_args()

    runtime_arn = args.runtime_arn or require_env("AGENT_RUNTIME_ARN")
    allow_role_name = validate_role_name(args.allow_role_name)
    deny_role_name = validate_role_name(args.deny_role_name)
    allow_user_name = validate_user_name(args.allow_user_name)
    deny_user_name = validate_user_name(args.deny_user_name)

    session = boto3.Session(profile_name=args.profile) if args.profile else boto3.Session()
    account_id = get_account_id(session)
    iam = session.client("iam")

    allow_role_arn = ensure_role(
        iam,
        allow_role_name,
        account_id,
        "Prompt 4 positive test identity. Can invoke the configured AgentCore Runtime.",
    )
    deny_role_arn = ensure_role(
        iam,
        deny_role_name,
        account_id,
        "Prompt 4 negative test identity. Explicitly denied runtime invocation.",
    )

    put_allow_policy(iam, allow_role_name, runtime_arn)
    put_explicit_deny_policy(iam, deny_role_name, runtime_arn)
    time.sleep(10)

    values = {
        "PROMPT4_CLIENT_RUNTIME_IAM_ROLE_ALLOW_ARN": allow_role_arn,
        "PROMPT4_CLIENT_RUNTIME_IAM_ROLE_DENY_ARN": deny_role_arn,
        "PROMPT4_ALLOW_ROLE_ARN": allow_role_arn,
        "PROMPT4_DENY_ROLE_ARN": deny_role_arn,
    }
    write_env_file(Path(args.env_file), values)

    for key, value in values.items():
        print(f"export {key}={value}")

    if args.include_iam_users:
        allow_user_arn = ensure_user(iam, allow_user_name)
        deny_user_arn = ensure_user(iam, deny_user_name)
        put_user_allow_policy(iam, allow_user_name, runtime_arn)
        put_user_explicit_deny_policy(iam, deny_user_name, runtime_arn)

        allow_key = ensure_user_access_key(iam, allow_user_name, args.rotate_iam_user_keys)
        deny_key = ensure_user_access_key(iam, deny_user_name, args.rotate_iam_user_keys)
        time.sleep(10)

        user_values = {
            "PROMPT4_CLIENT_RUNTIME_IAM_USER_ALLOW_ARN": allow_user_arn,
            "PROMPT4_CLIENT_RUNTIME_IAM_USER_ALLOW_ACCESS_KEY_ID": allow_key[
                "AccessKeyId"
            ],
            "PROMPT4_CLIENT_RUNTIME_IAM_USER_ALLOW_SECRET_ACCESS_KEY": allow_key[
                "SecretAccessKey"
            ],
            "PROMPT4_CLIENT_RUNTIME_IAM_USER_DENY_ARN": deny_user_arn,
            "PROMPT4_CLIENT_RUNTIME_IAM_USER_DENY_ACCESS_KEY_ID": deny_key[
                "AccessKeyId"
            ],
            "PROMPT4_CLIENT_RUNTIME_IAM_USER_DENY_SECRET_ACCESS_KEY": deny_key[
                "SecretAccessKey"
            ],
        }
        write_env_file(Path(args.user_env_file), user_values)

        for key, value in user_values.items():
            print(f"export {key}={printable_env_value(key, value, args.user_env_file)}")


if __name__ == "__main__":
    main()
