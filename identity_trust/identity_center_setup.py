import argparse
import json
import os
import time
from pathlib import Path
from typing import Any

import boto3
from botocore.exceptions import ClientError


BASE_DIR = Path(__file__).parent


def require_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"{name} is required")
    return value


def runtime_resources(runtime_arn: str) -> list[str]:
    return [
        runtime_arn,
        f"{runtime_arn}/runtime-endpoint/*",
    ]


def get_account_id(session: boto3.Session) -> str:
    return session.client("sts").get_caller_identity()["Account"]


def get_identity_center_instance(sso_admin) -> dict[str, str]:
    instances = sso_admin.list_instances()["Instances"]
    if not instances:
        raise RuntimeError("No IAM Identity Center instance found")
    if len(instances) > 1:
        raise RuntimeError("Multiple IAM Identity Center instances found; pass explicit values")
    return instances[0]


def permission_set_policy(effect: str, runtime_arn: str) -> dict[str, Any]:
    return {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": effect,
                "Action": ["bedrock-agentcore:InvokeAgentRuntime"],
                "Resource": runtime_resources(runtime_arn),
            }
        ],
    }


def find_permission_set_arn(sso_admin, instance_arn: str, name: str) -> str | None:
    paginator = sso_admin.get_paginator("list_permission_sets")
    for page in paginator.paginate(InstanceArn=instance_arn):
        for permission_set_arn in page["PermissionSets"]:
            permission_set = sso_admin.describe_permission_set(
                InstanceArn=instance_arn,
                PermissionSetArn=permission_set_arn,
            )["PermissionSet"]
            if permission_set["Name"] == name:
                return permission_set_arn
    return None


def ensure_permission_set(
    sso_admin,
    instance_arn: str,
    name: str,
    description: str,
    policy: dict[str, Any],
) -> str:
    permission_set_arn = find_permission_set_arn(sso_admin, instance_arn, name)
    if permission_set_arn:
        print(f"Using existing permission set: {permission_set_arn}")
    else:
        permission_set_arn = sso_admin.create_permission_set(
            InstanceArn=instance_arn,
            Name=name,
            Description=description,
            SessionDuration="PT1H",
            Tags=[{"Key": "ArchitecturePattern", "Value": "Prompt4IdentityTrust"}],
        )["PermissionSet"]["PermissionSetArn"]
        print(f"Created permission set: {permission_set_arn}")

    sso_admin.put_inline_policy_to_permission_set(
        InstanceArn=instance_arn,
        PermissionSetArn=permission_set_arn,
        InlinePolicy=json.dumps(policy),
    )
    return permission_set_arn


def list_identity_center_users(identitystore, identity_store_id: str) -> list[dict[str, Any]]:
    users = []
    paginator = identitystore.get_paginator("list_users")
    for page in paginator.paginate(IdentityStoreId=identity_store_id):
        users.extend(page["Users"])
    return users


def user_matches(user: dict[str, Any], lookup: str) -> bool:
    if user.get("UserId") == lookup or user.get("UserName") == lookup:
        return True
    return any(email.get("Value") == lookup for email in user.get("Emails", []))


def resolve_user(identitystore, identity_store_id: str, lookup: str) -> dict[str, Any]:
    matches = [
        user
        for user in list_identity_center_users(identitystore, identity_store_id)
        if user_matches(user, lookup)
    ]
    if not matches:
        raise RuntimeError(f"No Identity Center user matched {lookup}")
    if len(matches) > 1:
        raise RuntimeError(f"Multiple Identity Center users matched {lookup}")
    return matches[0]


def assignment_exists(
    sso_admin,
    instance_arn: str,
    account_id: str,
    permission_set_arn: str,
    principal_id: str,
) -> bool:
    paginator = sso_admin.get_paginator("list_account_assignments")
    for page in paginator.paginate(
        InstanceArn=instance_arn,
        AccountId=account_id,
        PermissionSetArn=permission_set_arn,
    ):
        for assignment in page["AccountAssignments"]:
            if (
                assignment["PrincipalType"] == "USER"
                and assignment["PrincipalId"] == principal_id
            ):
                return True
    return False


def wait_for_assignment(sso_admin, instance_arn: str, request_id: str) -> None:
    while True:
        status = sso_admin.describe_account_assignment_creation_status(
            InstanceArn=instance_arn,
            AccountAssignmentCreationRequestId=request_id,
        )["AccountAssignmentCreationStatus"]
        state = status["Status"]
        if state == "SUCCEEDED":
            return
        if state == "FAILED":
            raise RuntimeError(f"Account assignment failed: {status.get('FailureReason')}")
        time.sleep(5)


def ensure_assignment(
    sso_admin,
    instance_arn: str,
    account_id: str,
    permission_set_arn: str,
    user_id: str,
) -> None:
    if assignment_exists(sso_admin, instance_arn, account_id, permission_set_arn, user_id):
        print(f"Using existing account assignment: {permission_set_arn} -> {user_id}")
        return

    response = sso_admin.create_account_assignment(
        InstanceArn=instance_arn,
        TargetId=account_id,
        TargetType="AWS_ACCOUNT",
        PermissionSetArn=permission_set_arn,
        PrincipalType="USER",
        PrincipalId=user_id,
    )
    request_id = response["AccountAssignmentCreationStatus"]["RequestId"]
    wait_for_assignment(sso_admin, instance_arn, request_id)
    print(f"Created account assignment: {permission_set_arn} -> {user_id}")


def provision_permission_set(
    sso_admin,
    instance_arn: str,
    account_id: str,
    permission_set_arn: str,
) -> None:
    try:
        response = sso_admin.provision_permission_set(
            InstanceArn=instance_arn,
            PermissionSetArn=permission_set_arn,
            TargetType="AWS_ACCOUNT",
            TargetId=account_id,
        )
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") == "ConflictException":
            print(f"Provision already in progress for {permission_set_arn}")
            return
        raise

    request_id = response["PermissionSetProvisioningStatus"]["RequestId"]
    while True:
        status = sso_admin.describe_permission_set_provisioning_status(
            InstanceArn=instance_arn,
            ProvisionPermissionSetRequestId=request_id,
        )["PermissionSetProvisioningStatus"]
        state = status["Status"]
        if state == "SUCCEEDED":
            return
        if state == "FAILED":
            raise RuntimeError(f"Permission set provisioning failed: {status.get('FailureReason')}")
        time.sleep(5)


def write_env_file(path: Path, values: dict[str, str]) -> None:
    lines = [f"{key}={value}" for key, value in values.items()]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Create IAM Identity Center permission sets for Prompt 4 scenario 3."
    )
    parser.add_argument("--profile", help="AWS profile to use for setup.")
    parser.add_argument("--region", default=os.getenv("AWS_IDENTITY_CENTER_REGION", "us-east-1"))
    parser.add_argument("--runtime-arn", default=os.getenv("AGENT_RUNTIME_ARN"))
    parser.add_argument("--account-id", default=os.getenv("PROMPT4_AWS_ACCOUNT_ID"))
    parser.add_argument(
        "--user",
        default=os.getenv("PROMPT4_IDENTITY_CENTER_USER", "melon"),
        help="Identity Center user ID, username, or email to assign.",
    )
    parser.add_argument(
        "--allow-permission-set-name",
        default=os.getenv(
            "PROMPT4_IDENTITY_CENTER_ALLOW_PERMISSION_SET_NAME",
            "Prompt4RuntimeInvokeAllow",
        ),
    )
    parser.add_argument(
        "--deny-permission-set-name",
        default=os.getenv(
            "PROMPT4_IDENTITY_CENTER_DENY_PERMISSION_SET_NAME",
            "Prompt4RuntimeInvokeDeny",
        ),
    )
    parser.add_argument(
        "--env-file",
        default=str(BASE_DIR / "identity_center.env"),
        help="Write Identity Center setup values to this dotenv-style file.",
    )
    args = parser.parse_args()

    runtime_arn = args.runtime_arn or require_env("AGENT_RUNTIME_ARN")
    session = boto3.Session(profile_name=args.profile) if args.profile else boto3.Session()
    account_id = args.account_id or get_account_id(session)

    sso_admin = session.client("sso-admin", region_name=args.region)
    instance = get_identity_center_instance(sso_admin)
    instance_arn = instance["InstanceArn"]
    identity_store_id = instance["IdentityStoreId"]
    identitystore = session.client("identitystore", region_name=args.region)

    user = resolve_user(identitystore, identity_store_id, args.user)
    user_id = user["UserId"]

    allow_permission_set_arn = ensure_permission_set(
        sso_admin,
        instance_arn,
        args.allow_permission_set_name,
        "Allows invoking the Prompt 4 AgentCore Runtime.",
        permission_set_policy("Allow", runtime_arn),
    )
    deny_permission_set_arn = ensure_permission_set(
        sso_admin,
        instance_arn,
        args.deny_permission_set_name,
        "Explicitly denies invoking the Prompt 4 AgentCore Runtime.",
        permission_set_policy("Deny", runtime_arn),
    )

    ensure_assignment(sso_admin, instance_arn, account_id, allow_permission_set_arn, user_id)
    ensure_assignment(sso_admin, instance_arn, account_id, deny_permission_set_arn, user_id)

    provision_permission_set(sso_admin, instance_arn, account_id, allow_permission_set_arn)
    provision_permission_set(sso_admin, instance_arn, account_id, deny_permission_set_arn)

    values = {
        "PROMPT4_IDENTITY_CENTER_INSTANCE_ARN": instance_arn,
        "PROMPT4_IDENTITY_CENTER_IDENTITY_STORE_ID": identity_store_id,
        "PROMPT4_IDENTITY_CENTER_USER_ID": user_id,
        "PROMPT4_IDENTITY_CENTER_USER_NAME": user.get("UserName", ""),
        "PROMPT4_IDENTITY_CENTER_ALLOW_PERMISSION_SET_NAME": args.allow_permission_set_name,
        "PROMPT4_IDENTITY_CENTER_ALLOW_PERMISSION_SET_ARN": allow_permission_set_arn,
        "PROMPT4_IDENTITY_CENTER_DENY_PERMISSION_SET_NAME": args.deny_permission_set_name,
        "PROMPT4_IDENTITY_CENTER_DENY_PERMISSION_SET_ARN": deny_permission_set_arn,
        "PROMPT4_IDENTITY_CENTER_ALLOW_PROFILE": "prompt4-identity-center-allow",
        "PROMPT4_IDENTITY_CENTER_DENY_PROFILE": "prompt4-identity-center-deny",
    }
    write_env_file(Path(args.env_file), values)

    for key, value in values.items():
        print(f"export {key}={value}")


if __name__ == "__main__":
    main()
