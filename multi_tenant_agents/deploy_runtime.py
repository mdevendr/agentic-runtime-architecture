from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import stat
import subprocess
import time
import zipfile
from pathlib import Path

import boto3
from botocore.exceptions import ClientError


BASE_DIR = Path(__file__).resolve().parent
BUILD_DIR = BASE_DIR / "build"
PACKAGE_DIR = BUILD_DIR / "package"
ZIP_PATH = BUILD_DIR / "multi_tenant_runtime.zip"
RUNTIME_NAME_PATTERN = re.compile(r"^[a-zA-Z][a-zA-Z0-9_]{0,47}$")
DEFAULT_RUNTIME_NAME = "multi_tenant_agent_runtime"


def remove_readonly(func, path, _exc_info) -> None:
    os.chmod(path, stat.S_IWRITE)
    func(path)


def validate_runtime_name(name: str) -> str:
    if not RUNTIME_NAME_PATTERN.fullmatch(name):
        raise RuntimeError("Runtime name must match [a-zA-Z][a-zA-Z0-9_]{0,47}.")
    return name


def get_account_id(session: boto3.Session) -> str:
    return session.client("sts").get_caller_identity()["Account"]


def ensure_code_bucket(s3, bucket: str, region: str) -> None:
    try:
        s3.head_bucket(Bucket=bucket)
        print(f"Using existing code bucket: s3://{bucket}")
        return
    except ClientError as exc:
        status = exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
        code = exc.response.get("Error", {}).get("Code")
        if status not in {403, 404} and code not in {"403", "404", "NoSuchBucket"}:
            raise
        if status == 403 or code == "403":
            raise RuntimeError(f"Bucket {bucket} exists but is not accessible") from exc

    print(f"Creating code bucket: s3://{bucket}")
    if region == "us-east-1":
        s3.create_bucket(Bucket=bucket)
    else:
        s3.create_bucket(Bucket=bucket, CreateBucketConfiguration={"LocationConstraint": region})
    s3.get_waiter("bucket_exists").wait(Bucket=bucket)


def trust_policy(account_id: str, region: str) -> dict:
    return {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Principal": {"Service": "bedrock-agentcore.amazonaws.com"},
                "Action": "sts:AssumeRole",
                "Condition": {
                    "StringEquals": {"aws:SourceAccount": account_id},
                    "ArnLike": {"aws:SourceArn": f"arn:aws:bedrock-agentcore:{region}:{account_id}:*"},
                },
            }
        ],
    }


def execution_policy(account_id: str, region: str, bucket: str, key_prefix: str) -> dict:
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
                "Resource": [f"arn:aws:logs:{region}:{account_id}:log-group:/aws/bedrock-agentcore/runtimes/*"],
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
                "Condition": {"StringEquals": {"cloudwatch:namespace": "bedrock-agentcore"}},
            },
            {
                "Sid": "BedrockModelInvocation",
                "Effect": "Allow",
                "Action": ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"],
                "Resource": [
                    "arn:aws:bedrock:*::foundation-model/*",
                    f"arn:aws:bedrock:{region}:{account_id}:*",
                ],
            },
        ],
    }


def ensure_execution_role(
    session: boto3.Session,
    role_name: str,
    account_id: str,
    region: str,
    bucket: str,
    key_prefix: str,
) -> str:
    iam = session.client("iam")
    role_trust_policy = trust_policy(account_id, region)
    try:
        role = iam.get_role(RoleName=role_name)["Role"]
        print(f"Using existing execution role: {role['Arn']}")
        iam.update_assume_role_policy(RoleName=role_name, PolicyDocument=json.dumps(role_trust_policy))
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") != "NoSuchEntity":
            raise
        role = iam.create_role(
            RoleName=role_name,
            AssumeRolePolicyDocument=json.dumps(role_trust_policy),
            Description="Execution role for pooled multi-tenant AgentCore Runtime evidence.",
            Tags=[{"Key": "ArchitecturePattern", "Value": "MultiTenantAgentRuntime"}],
        )["Role"]

    iam.put_role_policy(
        RoleName=role_name,
        PolicyName="MultiTenantAgentRuntimePolicy",
        PolicyDocument=json.dumps(execution_policy(account_id, region, bucket, key_prefix)),
    )
    iam.get_waiter("role_exists").wait(RoleName=role_name)
    time.sleep(10)
    return role["Arn"]


def build_dependency_package() -> None:
    if PACKAGE_DIR.exists():
        shutil.rmtree(PACKAGE_DIR, onerror=remove_readonly)
    PACKAGE_DIR.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
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
            "bedrock-agentcore",
        ],
        check=True,
    )


def create_zip(skip_deps: bool) -> Path:
    BUILD_DIR.mkdir(exist_ok=True)
    if not skip_deps:
        build_dependency_package()

    with zipfile.ZipFile(ZIP_PATH, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        if PACKAGE_DIR.exists():
            for path in PACKAGE_DIR.rglob("*"):
                if path.is_file():
                    archive.write(path, path.relative_to(PACKAGE_DIR))

        for filename in [
            "runtime_with_llm.py",
            "cognito_claims.py",
            "dynamodb_tenant_store.py",
            "tenant_tool_policy.py",
            "pooled_runtime_demo.py",
        ]:
            archive.write(BASE_DIR / filename, filename)

    print(f"Created deployment package: {ZIP_PATH}")
    return ZIP_PATH


def wait_until_ready(control, runtime_id: str, runtime_version: str | None) -> dict:
    while True:
        request = {"agentRuntimeId": runtime_id}
        if runtime_version:
            request["agentRuntimeVersion"] = runtime_version
        response = control.get_agent_runtime(**request)
        status = response["status"]
        print(f"Runtime status: {status}")
        if status == "READY":
            return response
        if status in {"CREATE_FAILED", "UPDATE_FAILED", "DELETING"}:
            raise RuntimeError(f"Runtime did not become READY: {response}")
        time.sleep(20)


def find_runtime_by_name(control, runtime_name: str) -> dict | None:
    request: dict[str, str] = {}
    while True:
        response = control.list_agent_runtimes(**request)
        for runtime in response.get("agentRuntimes", []) + response.get("items", []):
            if runtime.get("agentRuntimeName") == runtime_name:
                return runtime
        next_token = response.get("nextToken")
        if not next_token:
            return None
        request["nextToken"] = next_token


def write_env(path: Path, values: dict[str, str]) -> None:
    path.write_text("\n".join(f"{key}={value}" for key, value in values.items()) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Deploy pooled multi-tenant AgentCore Runtime with LLM.")
    parser.add_argument("--profile", help="AWS profile to use.")
    parser.add_argument("--region", default=os.getenv("AWS_REGION", "eu-west-2"))
    parser.add_argument("--runtime-name", default=os.getenv("MULTI_TENANT_RUNTIME_NAME", DEFAULT_RUNTIME_NAME))
    parser.add_argument("--role-name", default=os.getenv("MULTI_TENANT_RUNTIME_ROLE_NAME"))
    parser.add_argument("--bucket", default=os.getenv("MULTI_TENANT_CODE_BUCKET"))
    parser.add_argument("--env-file", default=str(BASE_DIR / "multi_tenant_runtime.env"))
    parser.add_argument("--skip-deps", action="store_true")
    parser.add_argument("--no-wait", action="store_true")
    args = parser.parse_args()

    model_id = os.getenv("BEDROCK_MODEL_ID")
    if not model_id:
        raise RuntimeError("BEDROCK_MODEL_ID is required")

    runtime_name = validate_runtime_name(args.runtime_name)
    session = boto3.Session(profile_name=args.profile) if args.profile else boto3.Session()
    account_id = get_account_id(session)
    bucket = args.bucket or f"agentcore-multitenant-runtime-{account_id}-{args.region}"
    role_name = args.role_name or f"AmazonBedrockAgentCoreMultiTenantRuntime-{args.region}"
    key_prefix = runtime_name

    zip_path = create_zip(args.skip_deps)
    s3 = session.client("s3", region_name=args.region)
    control = session.client("bedrock-agentcore-control", region_name=args.region)
    ensure_code_bucket(s3, bucket, args.region)
    role_arn = ensure_execution_role(session, role_name, account_id, args.region, bucket, key_prefix)

    key = f"{key_prefix}/multi_tenant_runtime.zip"
    print(f"Uploading {zip_path} to s3://{bucket}/{key}")
    s3.upload_file(str(zip_path), bucket, key)

    runtime_config = {
        "agentRuntimeArtifact": {
            "codeConfiguration": {
                "code": {"s3": {"bucket": bucket, "prefix": key}},
                "runtime": "PYTHON_3_13",
                "entryPoint": ["runtime_with_llm.py"],
            }
        },
        "networkConfiguration": {"networkMode": "PUBLIC"},
        "roleArn": role_arn,
        "protocolConfiguration": {"serverProtocol": "HTTP"},
        "lifecycleConfiguration": {"idleRuntimeSessionTimeout": 300, "maxLifetime": 1800},
        "environmentVariables": {
            "AWS_REGION": args.region,
            "BEDROCK_MODEL_ID": model_id,
        },
    }

    try:
        response = control.create_agent_runtime(
            agentRuntimeName=runtime_name,
            **runtime_config,
            tags={"ArchitecturePattern": "MultiTenantAgentRuntime"},
        )
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") != "ConflictException":
            raise
        existing = find_runtime_by_name(control, runtime_name)
        if not existing:
            raise
        print(f"Updating existing Runtime: {existing['agentRuntimeId']}")
        response = control.update_agent_runtime(
            agentRuntimeId=existing["agentRuntimeId"],
            **runtime_config,
        )

    values = {
        "MULTI_TENANT_RUNTIME_ARN": response["agentRuntimeArn"],
        "MULTI_TENANT_RUNTIME_ID": response["agentRuntimeId"],
        "MULTI_TENANT_RUNTIME_VERSION": response.get("agentRuntimeVersion", ""),
        "MULTI_TENANT_RUNTIME_ROLE_ARN": role_arn,
    }
    write_env(Path(args.env_file), values)
    print(json.dumps(values, indent=2))

    if not args.no_wait:
        ready = wait_until_ready(control, response["agentRuntimeId"], response.get("agentRuntimeVersion"))
        print("Runtime is READY:")
        print(json.dumps(ready, indent=2, default=str))


if __name__ == "__main__":
    main()

