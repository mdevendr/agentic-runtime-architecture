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
IDENTITY_DIR = BASE_DIR.parent
BUILD_DIR = BASE_DIR / "build"
PACKAGE_DIR = BUILD_DIR / "package"
ZIP_PATH = BUILD_DIR / "caller_context_runtime.zip"
RUNTIME_NAME_PATTERN = re.compile(r"^[a-zA-Z][a-zA-Z0-9_]{0,47}$")
CALLER_CONTEXT_HEADER = "X-Amzn-Bedrock-AgentCore-Runtime-Custom-Caller-Context-Assertion"


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


def execution_role_trust_policy(account_id: str, region: str) -> dict:
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


def execution_role_policy(account_id: str, region: str, bucket: str, key_prefix: str) -> dict:
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
    trust_policy = execution_role_trust_policy(account_id, region)
    try:
        role = iam.get_role(RoleName=role_name)["Role"]
        print(f"Using existing execution role: {role['Arn']}")
        iam.update_assume_role_policy(RoleName=role_name, PolicyDocument=json.dumps(trust_policy))
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") != "NoSuchEntity":
            raise
        role = iam.create_role(
            RoleName=role_name,
            AssumeRolePolicyDocument=json.dumps(trust_policy),
            Description="Execution role for caller-context Runtime demo.",
            Tags=[{"Key": "ArchitecturePattern", "Value": "CallerContextSubstitutionDemo"}],
        )["Role"]

    iam.put_role_policy(
        RoleName=role_name,
        PolicyName="CallerContextRuntimePolicy",
        PolicyDocument=json.dumps(execution_role_policy(account_id, region, bucket, key_prefix)),
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
        archive.write(BASE_DIR / "app.py", "app.py")
        archive.write(IDENTITY_DIR / "caller_context_assertion.py", "caller_context_assertion.py")

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
    request: dict = {}
    while True:
        response = control.list_agent_runtimes(**request)
        for runtime in response.get("agentRuntimes", []) + response.get("items", []):
            if runtime.get("agentRuntimeName") == runtime_name:
                return runtime
        next_token = response.get("nextToken")
        if not next_token:
            return None
        request["nextToken"] = next_token


def write_env_file(path: Path, values: dict[str, str]) -> None:
    path.write_text("\n".join(f"{k}={v}" for k, v in values.items()) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Deploy caller-context Runtime demo.")
    parser.add_argument("--profile", help="AWS profile to use.")
    parser.add_argument("--region", default=os.getenv("AWS_REGION", "eu-west-2"))
    parser.add_argument("--runtime-name", default=os.getenv("CALLER_CONTEXT_RUNTIME_NAME", "caller_context_demo"))
    parser.add_argument("--role-name", default=os.getenv("CALLER_CONTEXT_RUNTIME_ROLE_NAME"))
    parser.add_argument("--bucket", default=os.getenv("CALLER_CONTEXT_CODE_BUCKET"))
    parser.add_argument("--env-file", default=str(BASE_DIR / "caller_context_runtime.env"))
    parser.add_argument("--skip-deps", action="store_true")
    parser.add_argument("--no-wait", action="store_true")
    args = parser.parse_args()

    args.runtime_name = validate_runtime_name(args.runtime_name)
    session = boto3.Session(profile_name=args.profile) if args.profile else boto3.Session()
    account_id = get_account_id(session)
    bucket = args.bucket or f"agentcore-caller-context-{account_id}-{args.region}"
    role_name = args.role_name or f"AmazonBedrockAgentCoreCallerContext-{args.region}"
    key_prefix = args.runtime_name

    zip_path = create_zip(args.skip_deps)
    s3 = session.client("s3", region_name=args.region)
    control = session.client("bedrock-agentcore-control", region_name=args.region)
    ensure_code_bucket(s3, bucket, args.region)
    role_arn = ensure_execution_role(session, role_name, account_id, args.region, bucket, key_prefix)

    key = f"{key_prefix}/caller_context_runtime.zip"
    print(f"Uploading {zip_path} to s3://{bucket}/{key}")
    s3.upload_file(str(zip_path), bucket, key)

    runtime_artifact = {
        "codeConfiguration": {
            "code": {"s3": {"bucket": bucket, "prefix": key}},
            "runtime": "PYTHON_3_13",
            "entryPoint": ["app.py"],
        }
    }
    runtime_config = {
        "agentRuntimeArtifact": runtime_artifact,
        "networkConfiguration": {"networkMode": "PUBLIC"},
        "roleArn": role_arn,
        "protocolConfiguration": {"serverProtocol": "HTTP"},
        "requestHeaderConfiguration": {"requestHeaderAllowlist": [CALLER_CONTEXT_HEADER]},
        "lifecycleConfiguration": {"idleRuntimeSessionTimeout": 300, "maxLifetime": 1800},
    }

    try:
        response = control.create_agent_runtime(
            agentRuntimeName=args.runtime_name,
            **runtime_config,
            tags={"ArchitecturePattern": "CallerContextSubstitutionDemo"},
        )
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") != "ConflictException":
            raise
        existing = find_runtime_by_name(control, args.runtime_name)
        if not existing:
            raise RuntimeError(
                f"Runtime {args.runtime_name} already exists but was not returned by ListAgentRuntimes."
            ) from exc
        runtime_id = existing["agentRuntimeId"]
        print(f"Updating existing Runtime with caller-context header allowlist: {runtime_id}")
        response = control.update_agent_runtime(
            agentRuntimeId=runtime_id,
            **runtime_config,
        )

    env_values = {
        "CALLER_CONTEXT_RUNTIME_ARN": response["agentRuntimeArn"],
        "CALLER_CONTEXT_RUNTIME_ID": response["agentRuntimeId"],
        "CALLER_CONTEXT_RUNTIME_VERSION": response.get("agentRuntimeVersion", ""),
        "CALLER_CONTEXT_RUNTIME_ROLE_ARN": role_arn,
    }
    write_env_file(Path(args.env_file), env_values)
    print(json.dumps(env_values, indent=2))

    if not args.no_wait:
        ready = wait_until_ready(control, response["agentRuntimeId"], response.get("agentRuntimeVersion"))
        print("Runtime is READY:")
        print(json.dumps(ready, indent=2, default=str))


if __name__ == "__main__":
    main()
