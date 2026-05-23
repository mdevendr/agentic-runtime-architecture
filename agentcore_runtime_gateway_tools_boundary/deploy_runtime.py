import argparse
import json
import os
import re
import shutil
import stat
import subprocess
import time
import zipfile
from botocore.exceptions import ClientError
from pathlib import Path

import boto3


BASE_DIR = Path(__file__).parent
BUILD_DIR = BASE_DIR / "build"
PACKAGE_DIR = BUILD_DIR / "package"
ZIP_PATH = BUILD_DIR / "agentcore_gateway_tools_boundary.zip"
DEFAULT_RUNTIME_NAME = "gateway_tools_boundary_agent"
RUNTIME_NAME_PATTERN = re.compile(r"^[a-zA-Z][a-zA-Z0-9_]{0,47}$")
ROLE_POLICY_NAME = "AgentCoreGatewayToolsBoundaryAgentPolicy"

def prompt_for_model_id() -> str:
    model_id = os.getenv("BEDROCK_MODEL_ID")
    if model_id:
        return model_id

    model_id = input("Bedrock model id with tool-use support: ").strip()
    if not model_id:
        raise RuntimeError("BEDROCK_MODEL_ID is required")
    return model_id


def remove_readonly(func, path, _exc_info) -> None:
    os.chmod(path, stat.S_IWRITE)
    func(path)


def validate_runtime_name(runtime_name: str) -> str:
    if not RUNTIME_NAME_PATTERN.fullmatch(runtime_name):
        raise RuntimeError(
            "AGENTCORE_RUNTIME_NAME must match [a-zA-Z][a-zA-Z0-9_]{0,47}. "
            "Use letters, numbers, and underscores only; hyphens are not allowed."
        )
    return runtime_name

def get_account_id() -> str:
    sts = boto3.client("sts")
    return sts.get_caller_identity()["Account"]

def default_code_bucket(account_id: str, region: str) -> str:
    return f"agentcore-gateway-boundary-{account_id}-{region}"

def default_role_name(region: str) -> str:
    return f"AmazonBedrockAgentCoreGatewayToolsAgent-{region}"

def ensure_code_bucket(s3, bucket: str, region: str) -> None:
    try:
        s3.head_bucket(Bucket=bucket)
        print(f"Using existing code bucket: s3://{bucket}")
        return
    except ClientError as exc:
        status = exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
        error_code = exc.response.get("Error", {}).get("Code")
        if status not in {403, 404} and error_code not in {"403", "404", "NoSuchBucket"}:
            raise
        if status == 403 or error_code == "403":
            raise RuntimeError(
                f"Bucket name {bucket} already exists but is not accessible. "
                "Choose a different runtime name or account/region."
            ) from exc

    print(f"Creating code bucket: s3://{bucket}")
    if region == "us-east-1":
        s3.create_bucket(Bucket=bucket)
    else:
        s3.create_bucket(
            Bucket=bucket,
            CreateBucketConfiguration={"LocationConstraint": region},
        )

    waiter = s3.get_waiter("bucket_exists")
    waiter.wait(Bucket=bucket)


def execution_role_trust_policy(account_id: str, region: str) -> dict:
    return {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Sid": "AssumeRolePolicy",
                "Effect": "Allow",
                "Principal": {"Service": "bedrock-agentcore.amazonaws.com"},
                "Action": "sts:AssumeRole",
                "Condition": {
                    "StringEquals": {"aws:SourceAccount": account_id},
                    "ArnLike": {
                        "aws:SourceArn": (
                            f"arn:aws:bedrock-agentcore:{region}:{account_id}:*"
                        )
                    },
                },
            }
        ],
    }


def execution_role_permissions(
    account_id: str,
    region: str,
    bucket: str,
    key_prefix: str,
) -> dict:
    gateway_resource = os.getenv("AGENTCORE_GATEWAY_ARN")
    authorizer = os.getenv("AGENTCORE_GATEWAY_AUTHORIZER", "AWS_IAM").upper()
    if authorizer == "AWS_IAM" and not gateway_resource:
        raise RuntimeError(
            "AGENTCORE_GATEWAY_ARN is required when AGENTCORE_GATEWAY_AUTHORIZER=AWS_IAM "
            "to enforce least-privilege runtime policy."
        )
    if not gateway_resource:
        gateway_resource = "*"

    return {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Sid": "ReadAgentCode",
                "Effect": "Allow",
                "Action": ["s3:GetObject"],
                "Resource": f"arn:aws:s3:::{bucket}/{key_prefix}/*",
            },
            {
                "Sid": "RuntimeLogGroupAccess",
                "Effect": "Allow",
                "Action": ["logs:DescribeLogStreams", "logs:CreateLogGroup"],
                "Resource": [
                    f"arn:aws:logs:{region}:{account_id}:log-group:/aws/bedrock-agentcore/runtimes/*"
                ],
            },
            {
                "Sid": "RuntimeLogDescribeAccess",
                "Effect": "Allow",
                "Action": ["logs:DescribeLogGroups"],
                "Resource": [f"arn:aws:logs:{region}:{account_id}:log-group:*"],
            },
            {
                "Sid": "RuntimeLogStreamAccess",
                "Effect": "Allow",
                "Action": ["logs:CreateLogStream", "logs:PutLogEvents"],
                "Resource": [
                    f"arn:aws:logs:{region}:{account_id}:log-group:/aws/bedrock-agentcore/runtimes/*:log-stream:*"
                ],
            },
            {
                "Sid": "RuntimeTracing",
                "Effect": "Allow",
                "Action": [
                    "xray:PutTraceSegments",
                    "xray:PutTelemetryRecords",
                    "xray:GetSamplingRules",
                    "xray:GetSamplingTargets",
                ],
                "Resource": ["*"],
            },
            {
                "Sid": "RuntimeMetrics",
                "Effect": "Allow",
                "Action": "cloudwatch:PutMetricData",
                "Resource": "*",
                "Condition": {
                    "StringEquals": {"cloudwatch:namespace": "bedrock-agentcore"}
                },
            },
            {
                "Sid": "BedrockModelInvocation",
                "Effect": "Allow",
                "Action": [
                    "bedrock:InvokeModel",
                    "bedrock:InvokeModelWithResponseStream",
                ],
                "Resource": [
                    "arn:aws:bedrock:*::foundation-model/*",
                    f"arn:aws:bedrock:{region}:{account_id}:*",
                ],
            },
            {
                "Sid": "InvokeAgentCoreGateway",
                "Effect": "Allow",
                "Action": ["bedrock-agentcore:InvokeGateway"],
                "Resource": gateway_resource,
            },
        ],
    }


def ensure_execution_role(
    iam,
    role_name: str,
    account_id: str,
    region: str,
    bucket: str,
    key_prefix: str,
) -> str:
    trust_policy = execution_role_trust_policy(account_id, region)

    try:
        role = iam.get_role(RoleName=role_name)["Role"]
        print(f"Using existing execution role: {role['Arn']}")
        iam.update_assume_role_policy(
            RoleName=role_name,
            PolicyDocument=json.dumps(trust_policy),
        )
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") != "NoSuchEntity":
            raise

        print(f"Creating execution role: {role_name}")
        role = iam.create_role(
            RoleName=role_name,
            AssumeRolePolicyDocument=json.dumps(trust_policy),
            Description="Execution role for the AgentCore Gateway tools boundary runtime.",
            Tags=[
                {
                    "Key": "ArchitecturePattern",
                    "Value": "AgentCoreGatewayToolsBoundary",
                }
            ],
        )["Role"]

    iam.put_role_policy(
        RoleName=role_name,
        PolicyName=ROLE_POLICY_NAME,
        PolicyDocument=json.dumps(
            execution_role_permissions(account_id, region, bucket, key_prefix)
        ),
    )

    waiter = iam.get_waiter("role_exists")
    waiter.wait(RoleName=role_name)
    time.sleep(10)
    return role["Arn"]


def build_dependency_package() -> None:
    if PACKAGE_DIR.exists():
        shutil.rmtree(PACKAGE_DIR, onerror=remove_readonly)

    PACKAGE_DIR.mkdir(parents=True, exist_ok=True)

    command = [
        "uv",
        "pip",
        "install",
        "--python-platform",
        "aarch64-manylinux2014",
        "--python-version",
        "3.13",
        "--target",
        str(PACKAGE_DIR),
        "--only-binary=:all:",
        "-r",
        str(BASE_DIR / "requirements.txt"),
    ]

    print("Installing Linux arm64 deployment dependencies with uv:")
    print(" ".join(command))
    subprocess.run(command, check=True)


def create_zip(skip_deps: bool = False) -> Path:
    BUILD_DIR.mkdir(exist_ok=True)

    if not skip_deps:
        build_dependency_package()

    with zipfile.ZipFile(ZIP_PATH, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        if PACKAGE_DIR.exists():
            for path in PACKAGE_DIR.rglob("*"):
                if path.is_file():
                    archive.write(path, path.relative_to(PACKAGE_DIR))

        archive.write(BASE_DIR / "main.py", "main.py")

    print(f"Created deployment package: {ZIP_PATH}")
    return ZIP_PATH


def wait_until_ready(client, runtime_id: str, runtime_version: str | None) -> dict:
    while True:
        request = {"agentRuntimeId": runtime_id}
        if runtime_version:
            request["agentRuntimeVersion"] = runtime_version

        response = client.get_agent_runtime(**request)
        status = response["status"]
        print(f"Runtime status: {status}")

        if status == "READY":
            return response

        if status in {"CREATE_FAILED", "UPDATE_FAILED", "DELETING"}:
            raise RuntimeError(f"Runtime did not become READY: {response}")

        time.sleep(20)


def main() -> None:
    parser = argparse.ArgumentParser(description="Deploy the AgentCore Gateway tools boundary runtime.")
    parser.add_argument("--no-wait", action="store_true", help="Do not wait for READY status.")
    parser.add_argument(
        "--skip-deps",
        action="store_true",
        help="Package only runtime source. Use only when dependencies are provided by another build path.",
    )
    args = parser.parse_args()

    region = os.getenv("AWS_REGION", "eu-west-2")
    runtime_name = validate_runtime_name(
        os.getenv("AGENTCORE_RUNTIME_NAME", DEFAULT_RUNTIME_NAME)
    )
    model_id = prompt_for_model_id()
    gateway_url = os.getenv("AGENTCORE_GATEWAY_URL")
    if not gateway_url:
        raise RuntimeError("AGENTCORE_GATEWAY_URL is required. Run setup_gateway.py first.")
    account_id = get_account_id()
    bucket = default_code_bucket(account_id, region)
    role_name = default_role_name(region)

    zip_path = create_zip(skip_deps=args.skip_deps)
    key = f"{runtime_name}/agentcore_gateway_tools_boundary.zip"

    s3 = boto3.client("s3", region_name=region)
    iam = boto3.client("iam")
    control = boto3.client("bedrock-agentcore-control", region_name=region)

    ensure_code_bucket(s3, bucket, region)
    role_arn = ensure_execution_role(
        iam,
        role_name,
        account_id,
        region,
        bucket,
        runtime_name,
    )

    print(f"Uploading {zip_path} to s3://{bucket}/{key}")
    s3.upload_file(str(zip_path), bucket, key)

    response = control.create_agent_runtime(
        agentRuntimeName=runtime_name,
        agentRuntimeArtifact={
            "codeConfiguration": {
                "code": {"s3": {"bucket": bucket, "prefix": key}},
                "runtime": "PYTHON_3_13",
                "entryPoint": ["main.py"],
            }
        },
        networkConfiguration={"networkMode": "PUBLIC"},
        roleArn=role_arn,
        protocolConfiguration={"serverProtocol": "HTTP"},
        lifecycleConfiguration={
            "idleRuntimeSessionTimeout": 300,
            "maxLifetime": 1800,
        },
        environmentVariables={
            "AWS_REGION": region,
            "BEDROCK_MODEL_ID": model_id,
            "AGENTCORE_GATEWAY_URL": gateway_url,
            "AGENTCORE_GATEWAY_AUTHORIZER": os.getenv("AGENTCORE_GATEWAY_AUTHORIZER", "AWS_IAM"),
        },
        tags={
            "ArchitecturePattern": "AgentCoreGatewayToolsBoundary",
        },
    )

    print("AgentCore Runtime create response:")
    print(response)
    print(f"AGENT_RUNTIME_ARN={response['agentRuntimeArn']}")
    print(f"AGENT_RUNTIME_ID={response['agentRuntimeId']}")
    print(f"AGENT_RUNTIME_VERSION={response.get('agentRuntimeVersion', '')}")

    if not args.no_wait:
        ready = wait_until_ready(
            control,
            response["agentRuntimeId"],
            response.get("agentRuntimeVersion"),
        )
        print("Runtime is READY:")
        print(ready)


if __name__ == "__main__":
    main()
