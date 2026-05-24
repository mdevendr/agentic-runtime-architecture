from __future__ import annotations

import argparse
import json
import os
import re
import time
import zipfile
from pathlib import Path
from typing import Any

import boto3
from botocore.exceptions import ClientError


BASE_DIR = Path(__file__).resolve().parent
IDENTITY_DIR = BASE_DIR.parent
BUILD_DIR = BASE_DIR / "build"
INTERCEPTOR_ZIP = BUILD_DIR / "caller_context_interceptor.zip"
CALLER_CONTEXT_HEADER = "X-Amzn-Bedrock-AgentCore-Runtime-Custom-Caller-Context-Assertion"

GATEWAY_NAME_PATTERN = re.compile(r"^([0-9a-zA-Z]-?){1,48}$")
TARGET_NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
ROLE_NAME_PATTERN = re.compile(r"^[\w+=,.@-]{1,64}$")
LAMBDA_NAME_PATTERN = re.compile(r"^[A-Za-z0-9-_]{1,64}$")


def load_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip()
    return values


def validate_name(name: str, label: str, pattern: re.Pattern[str]) -> str:
    if not pattern.fullmatch(name):
        raise RuntimeError(f"{label} has an invalid name: {name}")
    return name


def get_account_id(session: boto3.Session) -> str:
    return session.client("sts").get_caller_identity()["Account"]


def runtime_resources(runtime_arn: str) -> list[str]:
    return [runtime_arn, f"{runtime_arn}/runtime-endpoint/*"]


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
                    "ArnLike": {"aws:SourceArn": f"arn:aws:bedrock-agentcore:{region}:{account_id}:*"},
                },
            }
        ],
    }


def gateway_policy(runtime_arn: str, interceptor_arn: str) -> dict[str, Any]:
    return {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Sid": "GatewayInvokeRuntimeTarget",
                "Effect": "Allow",
                "Action": ["bedrock-agentcore:InvokeAgentRuntime"],
                "Resource": runtime_resources(runtime_arn),
            },
            {
                "Sid": "GatewayInvokeCallerContextInterceptor",
                "Effect": "Allow",
                "Action": ["lambda:InvokeFunction"],
                "Resource": [interceptor_arn],
            },
        ],
    }


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


def lambda_logs_policy(account_id: str, region: str, function_name: str) -> dict[str, Any]:
    return {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Sid": "WriteInterceptorLogs",
                "Effect": "Allow",
                "Action": ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"],
                "Resource": [
                    f"arn:aws:logs:{region}:{account_id}:log-group:/aws/lambda/{function_name}",
                    f"arn:aws:logs:{region}:{account_id}:log-group:/aws/lambda/{function_name}:*",
                ],
            }
        ],
    }


def ensure_lambda_role(
    session: boto3.Session,
    role_name: str,
    account_id: str,
    region: str,
    function_name: str,
) -> str:
    iam = session.client("iam")
    try:
        role = iam.get_role(RoleName=role_name)["Role"]
        print(f"Using existing interceptor Lambda role: {role['Arn']}")
        iam.update_assume_role_policy(
            RoleName=role_name,
            PolicyDocument=json.dumps(lambda_trust_policy()),
        )
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") != "NoSuchEntity":
            raise
        role = iam.create_role(
            RoleName=role_name,
            AssumeRolePolicyDocument=json.dumps(lambda_trust_policy()),
            Description="Lambda role for Gateway caller-context signing interceptor.",
            Tags=[{"Key": "ArchitecturePattern", "Value": "GatewaySignedCallerContext"}],
        )["Role"]

    iam.put_role_policy(
        RoleName=role_name,
        PolicyName="CallerContextInterceptorLogs",
        PolicyDocument=json.dumps(lambda_logs_policy(account_id, region, function_name)),
    )
    iam.get_waiter("role_exists").wait(RoleName=role_name)
    time.sleep(10)
    return role["Arn"]


def build_interceptor_zip() -> Path:
    BUILD_DIR.mkdir(exist_ok=True)
    with zipfile.ZipFile(INTERCEPTOR_ZIP, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.write(BASE_DIR / "gateway_caller_context_interceptor.py", "gateway_caller_context_interceptor.py")
        archive.write(IDENTITY_DIR / "caller_context_assertion.py", "caller_context_assertion.py")
    print(f"Created interceptor package: {INTERCEPTOR_ZIP}")
    return INTERCEPTOR_ZIP


def ensure_interceptor_lambda(
    session: boto3.Session,
    function_name: str,
    role_arn: str,
    runtime_audience: str,
    signing_secret: str,
) -> str:
    lambda_client = session.client("lambda")
    zip_path = build_interceptor_zip()
    code = zip_path.read_bytes()
    environment = {
        "Variables": {
            "CALLER_CONTEXT_RUNTIME_AUDIENCE": runtime_audience,
            "CALLER_CONTEXT_SIGNING_SECRET": signing_secret,
            "CALLER_CONTEXT_TTL_SECONDS": "300",
        }
    }

    try:
        function = lambda_client.get_function(FunctionName=function_name)["Configuration"]
        print(f"Updating existing interceptor Lambda: {function['FunctionArn']}")
        lambda_client.update_function_code(FunctionName=function_name, ZipFile=code, Publish=True)
        waiter = lambda_client.get_waiter("function_updated")
        waiter.wait(FunctionName=function_name)
        lambda_client.update_function_configuration(
            FunctionName=function_name,
            Role=role_arn,
            Handler="gateway_caller_context_interceptor.lambda_handler",
            Runtime="python3.12",
            Timeout=10,
            Environment=environment,
        )
        waiter.wait(FunctionName=function_name)
        return function["FunctionArn"]
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") != "ResourceNotFoundException":
            raise

    function = lambda_client.create_function(
        FunctionName=function_name,
        Runtime="python3.12",
        Role=role_arn,
        Handler="gateway_caller_context_interceptor.lambda_handler",
        Code={"ZipFile": code},
        Description="Signs caller-context assertions for AgentCore Gateway substitution evidence.",
        Timeout=10,
        Environment=environment,
        Tags={"ArchitecturePattern": "GatewaySignedCallerContext"},
    )
    lambda_client.get_waiter("function_active").wait(FunctionName=function_name)
    print(f"Created interceptor Lambda: {function['FunctionArn']}")
    return function["FunctionArn"]


def ensure_gateway_role(
    session: boto3.Session,
    role_name: str,
    runtime_arn: str,
    interceptor_arn: str,
    account_id: str,
    region: str,
) -> str:
    iam = session.client("iam")
    trust_policy = gateway_role_trust_policy(account_id, region)
    try:
        role = iam.get_role(RoleName=role_name)["Role"]
        print(f"Using existing Gateway role: {role['Arn']}")
        iam.update_assume_role_policy(RoleName=role_name, PolicyDocument=json.dumps(trust_policy))
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") != "NoSuchEntity":
            raise
        role = iam.create_role(
            RoleName=role_name,
            AssumeRolePolicyDocument=json.dumps(trust_policy),
            Description="AgentCore Gateway role for Runtime substitution with caller-context interceptor.",
            Tags=[{"Key": "ArchitecturePattern", "Value": "GatewaySignedCallerContext"}],
        )["Role"]

    iam.put_role_policy(
        RoleName=role_name,
        PolicyName="CallerContextGatewayPolicy",
        PolicyDocument=json.dumps(gateway_policy(runtime_arn, interceptor_arn)),
    )
    iam.get_waiter("role_exists").wait(RoleName=role_name)
    time.sleep(10)
    return role["Arn"]


def runtime_target_configuration(runtime_arn: str, qualifier: str | None = None) -> dict[str, Any]:
    target: dict[str, Any] = {"http": {"agentcoreRuntime": {"arn": runtime_arn}}}
    if qualifier:
        target["http"]["agentcoreRuntime"]["qualifier"] = qualifier
    return target


def gateway_interceptor_configuration(interceptor_arn: str) -> list[dict[str, Any]]:
    return [
        {
            "interceptor": {"lambda": {"arn": interceptor_arn}},
            "interceptionPoints": ["REQUEST"],
            "inputConfiguration": {"passRequestHeaders": True},
        }
    ]


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
                return control.get_gateway_target(gatewayIdentifier=gateway_id, targetId=target["targetId"])
        next_token = response.get("nextToken")
        if not next_token:
            return None
        request["nextToken"] = next_token


def wait_for_gateway_ready(control, gateway_id: str) -> dict[str, Any]:
    while True:
        response = control.get_gateway(gatewayIdentifier=gateway_id)
        status = response["status"]
        print(f"Gateway status: {status}")
        if status == "READY":
            return response
        if status in {"CREATE_FAILED", "UPDATE_FAILED", "DELETING"}:
            raise RuntimeError(f"Gateway did not become READY: {response}")
        time.sleep(10)


def wait_for_target_ready(control, gateway_id: str, target_id: str) -> dict[str, Any]:
    while True:
        response = control.get_gateway_target(gatewayIdentifier=gateway_id, targetId=target_id)
        status = response["status"]
        print(f"Gateway target status: {status}")
        if status == "READY":
            return response
        if status in {"CREATE_FAILED", "UPDATE_FAILED", "DELETING"}:
            raise RuntimeError(f"Gateway target did not become READY: {response}")
        time.sleep(10)


def create_gateway(control, gateway_name: str, role_arn: str, interceptor_arn: str) -> dict[str, Any]:
    try:
        response = control.create_gateway(
            name=gateway_name,
            description="Gateway-fronted Runtime with REQUEST interceptor signing caller context.",
            roleArn=role_arn,
            authorizerType="AWS_IAM",
            interceptorConfigurations=gateway_interceptor_configuration(interceptor_arn),
            exceptionLevel="DEBUG",
            tags={
                "ArchitecturePattern": "GatewaySignedCallerContext",
                "TargetAuthMode": "GatewayServiceRoleIamSigV4",
            },
        )
        print("Gateway create response:")
        print(json.dumps(response, indent=2, default=str))
        return response
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") != "ConflictException":
            raise
        existing = find_gateway_by_name(control, gateway_name)
        if not existing:
            raise
        print(f"Updating existing Gateway with caller-context interceptor: {existing['gatewayArn']}")
        response = control.update_gateway(
            gatewayIdentifier=existing["gatewayId"],
            name=gateway_name,
            description="Gateway-fronted Runtime with REQUEST interceptor signing caller context.",
            roleArn=role_arn,
            authorizerType="AWS_IAM",
            interceptorConfigurations=gateway_interceptor_configuration(interceptor_arn),
            exceptionLevel="DEBUG",
        )
        print("Gateway update response:")
        print(json.dumps(response, indent=2, default=str))
        return response


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
            description="Runtime target receiving Gateway-signed caller-context header.",
            targetConfiguration=runtime_target_configuration(runtime_arn, qualifier),
            credentialProviderConfigurations=[{"credentialProviderType": "GATEWAY_IAM_ROLE"}],
            metadataConfiguration={"allowedRequestHeaders": [CALLER_CONTEXT_HEADER]},
        )
        print("Runtime target create response:")
        print(json.dumps(response, indent=2, default=str))
        return response
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") != "ConflictException":
            raise
        existing = find_target_by_name(control, gateway_id, target_name)
        if not existing:
            raise
        print(f"Updating existing Runtime target with caller-context header allowlist: {existing['targetId']}")
        response = control.update_gateway_target(
            gatewayIdentifier=gateway_id,
            targetId=existing["targetId"],
            name=target_name,
            description="Runtime target receiving Gateway-signed caller-context header.",
            targetConfiguration=runtime_target_configuration(runtime_arn, qualifier),
            credentialProviderConfigurations=[{"credentialProviderType": "GATEWAY_IAM_ROLE"}],
            metadataConfiguration={"allowedRequestHeaders": [CALLER_CONTEXT_HEADER]},
        )
        print("Runtime target update response:")
        print(json.dumps(response, indent=2, default=str))
        return response


def write_env_file(path: Path, values: dict[str, str]) -> None:
    path.write_text("\n".join(f"{key}={value}" for key, value in values.items()) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Create Gateway substitution target with REQUEST interceptor signed caller context."
    )
    parser.add_argument("--profile", help="AWS profile to use.")
    parser.add_argument("--region", default=os.getenv("AWS_REGION", "eu-west-2"))
    parser.add_argument("--runtime-env-file", default=str(BASE_DIR / "caller_context_runtime.env"))
    parser.add_argument("--runtime-arn", default=os.getenv("CALLER_CONTEXT_RUNTIME_ARN"))
    parser.add_argument("--qualifier", default=os.getenv("CALLER_CONTEXT_RUNTIME_QUALIFIER"))
    parser.add_argument("--gateway-name", default="caller-context-header-frontdoor")
    parser.add_argument("--target-name", default="callerContextHeaderRuntimeTarget")
    parser.add_argument("--gateway-role-name", default="CallerContextHeaderGatewayRole")
    parser.add_argument("--interceptor-function-name", default="caller-context-header-interceptor")
    parser.add_argument("--interceptor-role-name", default="CallerContextHeaderInterceptorRole")
    parser.add_argument("--signing-secret", default=os.getenv("CALLER_CONTEXT_SIGNING_SECRET", "demo-signing-secret"))
    parser.add_argument("--env-file", default=str(BASE_DIR / "caller_context_gateway_header.env"))
    args = parser.parse_args()

    args.gateway_name = validate_name(args.gateway_name, "Gateway name", GATEWAY_NAME_PATTERN)
    args.target_name = validate_name(args.target_name, "Target name", TARGET_NAME_PATTERN)
    args.gateway_role_name = validate_name(args.gateway_role_name, "Gateway role name", ROLE_NAME_PATTERN)
    args.interceptor_role_name = validate_name(args.interceptor_role_name, "Interceptor role name", ROLE_NAME_PATTERN)
    args.interceptor_function_name = validate_name(
        args.interceptor_function_name,
        "Interceptor function name",
        LAMBDA_NAME_PATTERN,
    )

    runtime_env = load_env_file(Path(args.runtime_env_file))
    runtime_arn = args.runtime_arn or runtime_env.get("CALLER_CONTEXT_RUNTIME_ARN")
    if not runtime_arn:
        raise RuntimeError("CALLER_CONTEXT_RUNTIME_ARN is required. Deploy the Runtime first.")

    runtime_audience = f"agentcore-runtime:{runtime_arn}"
    session = boto3.Session(profile_name=args.profile) if args.profile else boto3.Session()
    account_id = get_account_id(session)

    interceptor_role_arn = ensure_lambda_role(
        session,
        args.interceptor_role_name,
        account_id,
        args.region,
        args.interceptor_function_name,
    )
    interceptor_arn = ensure_interceptor_lambda(
        session,
        args.interceptor_function_name,
        interceptor_role_arn,
        runtime_audience,
        args.signing_secret,
    )
    gateway_role_arn = ensure_gateway_role(
        session,
        args.gateway_role_name,
        runtime_arn,
        interceptor_arn,
        account_id,
        args.region,
    )

    control = session.client("bedrock-agentcore-control", region_name=args.region)
    gateway = create_gateway(control, args.gateway_name, gateway_role_arn, interceptor_arn)
    ready_gateway = wait_for_gateway_ready(control, gateway["gatewayId"])
    target = create_runtime_target(control, ready_gateway["gatewayId"], args.target_name, runtime_arn, args.qualifier)
    ready_target = wait_for_target_ready(control, ready_gateway["gatewayId"], target["targetId"])

    env_values = {
        "CALLER_CONTEXT_HEADER_GATEWAY_ID": ready_gateway["gatewayId"],
        "CALLER_CONTEXT_HEADER_GATEWAY_ARN": ready_gateway["gatewayArn"],
        "CALLER_CONTEXT_HEADER_GATEWAY_URL": ready_gateway.get("gatewayUrl", ""),
        "CALLER_CONTEXT_HEADER_TARGET_ID": ready_target["targetId"],
        "CALLER_CONTEXT_HEADER_TARGET_NAME": args.target_name,
        "CALLER_CONTEXT_HEADER_GATEWAY_ROLE_ARN": gateway_role_arn,
        "CALLER_CONTEXT_HEADER_INTERCEPTOR_ARN": interceptor_arn,
    }
    write_env_file(Path(args.env_file), env_values)
    print(json.dumps({"scenario": "gateway_signed_caller_context_header", "status": "created", **env_values}, indent=2))


if __name__ == "__main__":
    main()
