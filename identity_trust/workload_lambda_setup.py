import argparse
import json
import os
import time
import zipfile
from pathlib import Path
from typing import Any

import boto3
from botocore.exceptions import ClientError


BASE_DIR = Path(__file__).parent
BUILD_DIR = BASE_DIR / "build"
LAMBDA_SOURCE = BASE_DIR / "lambda_invoke_runtime_workload.py"


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


def lambda_trust_policy() -> dict[str, Any]:
    return {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Principal": {"Service": "lambda.amazonaws.com"},
                "Action": "sts:AssumeRole",
            }
        ],
    }


def logs_policy() -> dict[str, Any]:
    return {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Action": [
                    "logs:CreateLogGroup",
                    "logs:CreateLogStream",
                    "logs:PutLogEvents",
                ],
                "Resource": "*",
            }
        ],
    }


def runtime_policy(effect: str, runtime_arn: str) -> dict[str, Any]:
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


def ensure_role(iam, role_name: str, runtime_arn: str, effect: str) -> str:
    try:
        role = iam.get_role(RoleName=role_name)["Role"]
        iam.update_assume_role_policy(
            RoleName=role_name,
            PolicyDocument=json.dumps(lambda_trust_policy()),
        )
        print(f"Using existing Lambda workload role: {role['Arn']}")
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") != "NoSuchEntity":
            raise
        role = iam.create_role(
            RoleName=role_name,
            AssumeRolePolicyDocument=json.dumps(lambda_trust_policy()),
            Description="Prompt 4 Lambda workload role for AgentCore Runtime trust tests.",
            Tags=[{"Key": "ArchitecturePattern", "Value": "Prompt4IdentityTrust"}],
        )["Role"]
        print(f"Created Lambda workload role: {role['Arn']}")

    iam.put_role_policy(
        RoleName=role_name,
        PolicyName="Prompt4LambdaWorkloadLogs",
        PolicyDocument=json.dumps(logs_policy()),
    )
    iam.put_role_policy(
        RoleName=role_name,
        PolicyName=f"{effect}InvokePrompt4Runtime",
        PolicyDocument=json.dumps(runtime_policy(effect, runtime_arn)),
    )
    iam.put_role_policy(
        RoleName=role_name,
        PolicyName="Prompt4LambdaVpcNetworking",
        PolicyDocument=json.dumps(
            {
                "Version": "2012-10-17",
                "Statement": [
                    {
                        "Effect": "Allow",
                        "Action": [
                            "ec2:CreateNetworkInterface",
                            "ec2:DescribeNetworkInterfaces",
                            "ec2:DeleteNetworkInterface",
                            "ec2:AssignPrivateIpAddresses",
                            "ec2:UnassignPrivateIpAddresses",
                        ],
                        "Resource": "*",
                    }
                ],
            }
        ),
    )
    iam.get_waiter("role_exists").wait(RoleName=role_name)
    return role["Arn"]


def create_lambda_zip() -> Path:
    BUILD_DIR.mkdir(exist_ok=True)
    zip_path = BUILD_DIR / "lambda_invoke_runtime_workload.zip"
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.write(LAMBDA_SOURCE, "lambda_invoke_runtime_workload.py")
    return zip_path


def ensure_function(
    lambda_client,
    function_name: str,
    role_arn: str,
    runtime_arn: str,
    region: str,
    zip_path: Path,
    subnet_ids: list[str] | None = None,
    security_group_ids: list[str] | None = None,
) -> str:
    with zip_path.open("rb") as package:
        code_bytes = package.read()

    environment = {
        "Variables": {
            "AGENT_RUNTIME_ARN": runtime_arn,
        }
    }

    try:
        function = lambda_client.get_function(FunctionName=function_name)["Configuration"]
        lambda_client.update_function_code(
            FunctionName=function_name,
            ZipFile=code_bytes,
            Publish=True,
        )
        waiter = lambda_client.get_waiter("function_updated")
        waiter.wait(FunctionName=function_name)
        update_kwargs = {
            "FunctionName": function_name,
            "Role": role_arn,
            "Handler": "lambda_invoke_runtime_workload.handler",
            "Runtime": "python3.12",
            "Timeout": 120,
            "Environment": environment,
        }
        if subnet_ids and security_group_ids:
            update_kwargs["VpcConfig"] = {
                "SubnetIds": subnet_ids,
                "SecurityGroupIds": security_group_ids,
            }
        lambda_client.update_function_configuration(
            **update_kwargs,
        )
        waiter.wait(FunctionName=function_name)
        print(f"Using existing Lambda workload function: {function['FunctionArn']}")
        return function["FunctionArn"]
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") != "ResourceNotFoundException":
            raise

    create_kwargs = {
        "FunctionName": function_name,
        "Runtime": "python3.12",
        "Role": role_arn,
        "Handler": "lambda_invoke_runtime_workload.handler",
        "Code": {"ZipFile": code_bytes},
        "Timeout": 120,
        "Environment": environment,
        "Tags": {"ArchitecturePattern": "Prompt4IdentityTrust"},
        "Publish": True,
    }
    if subnet_ids and security_group_ids:
        create_kwargs["VpcConfig"] = {
            "SubnetIds": subnet_ids,
            "SecurityGroupIds": security_group_ids,
        }
    function = lambda_client.create_function(**create_kwargs)
    lambda_client.get_waiter("function_active").wait(FunctionName=function_name)
    print(f"Created Lambda workload function: {function['FunctionArn']}")
    return function["FunctionArn"]


def write_env_file(path: Path, values: dict[str, str]) -> None:
    lines = [f"{key}={value}" for key, value in values.items()]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Create Lambda workload functions for Prompt 4 scenario 4."
    )
    parser.add_argument("--profile", help="AWS profile to use for setup.")
    parser.add_argument("--region", default=os.getenv("AWS_REGION", "eu-west-2"))
    parser.add_argument("--runtime-arn", default=os.getenv("AGENT_RUNTIME_ARN_IAM") or os.getenv("AGENT_RUNTIME_ARN"))
    parser.add_argument(
        "--subnet-ids",
        nargs="*",
        default=[item.strip() for item in os.getenv("PROMPT4_PRIVATE_SUBNET_IDS", "").split(",") if item.strip()],
    )
    parser.add_argument(
        "--security-group-ids",
        nargs="*",
        default=[item.strip() for item in os.getenv("PROMPT4_PRIVATE_SECURITY_GROUP_IDS", "").split(",") if item.strip()],
    )
    parser.add_argument(
        "--allow-function-name",
        default=os.getenv(
            "PROMPT4_LAMBDA_WORKLOAD_ALLOW_FUNCTION_NAME",
            "prompt4-runtime-workload-allow",
        ),
    )
    parser.add_argument(
        "--deny-function-name",
        default=os.getenv(
            "PROMPT4_LAMBDA_WORKLOAD_DENY_FUNCTION_NAME",
            "prompt4-runtime-workload-deny",
        ),
    )
    parser.add_argument(
        "--allow-role-name",
        default=os.getenv(
            "PROMPT4_LAMBDA_WORKLOAD_ALLOW_ROLE_NAME",
            "Prompt4LambdaRuntimeWorkloadAllow",
        ),
    )
    parser.add_argument(
        "--deny-role-name",
        default=os.getenv(
            "PROMPT4_LAMBDA_WORKLOAD_DENY_ROLE_NAME",
            "Prompt4LambdaRuntimeWorkloadDeny",
        ),
    )
    parser.add_argument(
        "--env-file",
        default=str(BASE_DIR / "workload_lambda.env"),
        help="Write Lambda workload values to this dotenv-style file.",
    )
    args = parser.parse_args()

    runtime_arn = args.runtime_arn or require_env("AGENT_RUNTIME_ARN_IAM")
    session = boto3.Session(profile_name=args.profile) if args.profile else boto3.Session()
    iam = session.client("iam")
    lambda_client = session.client("lambda", region_name=args.region)

    allow_role_arn = ensure_role(iam, args.allow_role_name, runtime_arn, "Allow")
    deny_role_arn = ensure_role(iam, args.deny_role_name, runtime_arn, "Deny")
    time.sleep(10)

    zip_path = create_lambda_zip()
    allow_function_arn = ensure_function(
        lambda_client,
        args.allow_function_name,
        allow_role_arn,
        runtime_arn,
        args.region,
        zip_path,
        args.subnet_ids,
        args.security_group_ids,
    )
    deny_function_arn = ensure_function(
        lambda_client,
        args.deny_function_name,
        deny_role_arn,
        runtime_arn,
        args.region,
        zip_path,
        args.subnet_ids,
        args.security_group_ids,
    )

    values = {
        "PROMPT4_LAMBDA_WORKLOAD_ALLOW_FUNCTION_NAME": args.allow_function_name,
        "PROMPT4_LAMBDA_WORKLOAD_ALLOW_FUNCTION_ARN": allow_function_arn,
        "PROMPT4_LAMBDA_WORKLOAD_ALLOW_ROLE_ARN": allow_role_arn,
        "PROMPT4_LAMBDA_WORKLOAD_DENY_FUNCTION_NAME": args.deny_function_name,
        "PROMPT4_LAMBDA_WORKLOAD_DENY_FUNCTION_ARN": deny_function_arn,
        "PROMPT4_LAMBDA_WORKLOAD_DENY_ROLE_ARN": deny_role_arn,
        "AGENT_RUNTIME_ARN_IAM": runtime_arn,
    }
    write_env_file(Path(args.env_file), values)

    for key, value in values.items():
        print(f"export {key}={value}")


if __name__ == "__main__":
    main()
