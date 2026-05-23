import argparse
import json
import os
import re
import time
import zipfile
from pathlib import Path

import boto3
from botocore.exceptions import ClientError


BASE_DIR = Path(__file__).parent
BUILD_DIR = BASE_DIR / "build"
TOOL_SCHEMA_PATH = BASE_DIR / "tool_schema.json"

DEFAULT_GATEWAY_NAME = "gateway-tools-boundary"
DEFAULT_LAMBDA_NAME_CALCULATE = "agentcore_gateway_calculate_order_total"
DEFAULT_LAMBDA_NAME_REFUND = "agentcore_gateway_check_refund_eligibility"
GATEWAY_NAME_PATTERN = re.compile(r"^([0-9a-zA-Z]-?){1,48}$")
LAMBDA_NAME_PATTERN = re.compile(r"^[a-zA-Z0-9-_]{1,64}$")

# Tool definitions for two per-tool Lambda targets
TOOLS = [
    {
        "name": "calculate_order_total",
        "lambda_file": BASE_DIR / "lambda_calculate_order_total.py",
        "default_lambda_name": DEFAULT_LAMBDA_NAME_CALCULATE,
        "description": "Lambda target that owns calculate_order_total execution.",
    },
    {
        "name": "check_refund_eligibility",
        "lambda_file": BASE_DIR / "lambda_check_refund_eligibility.py",
        "default_lambda_name": DEFAULT_LAMBDA_NAME_REFUND,
        "description": "Lambda target that owns check_refund_eligibility execution.",
    },
]


def validate_name(name: str, label: str, pattern: re.Pattern[str], rule: str) -> str:
    if not pattern.fullmatch(name):
        raise RuntimeError(
            f"{label} must match {rule}."
        )
    return name


def get_account_id() -> str:
    return boto3.client("sts").get_caller_identity()["Account"]


def lambda_role_name(region: str, tool_name: str) -> str:
    return f"AgentCoreGateway{tool_name.replace('_', '').title()}LambdaRole-{region}"


def gateway_role_name(region: str) -> str:
    return f"AgentCoreGatewayBoundaryRole-{region}"


def create_lambda_zip(tool_name: str, lambda_file: Path) -> Path:
    BUILD_DIR.mkdir(exist_ok=True)
    zip_filename = f"gateway_lambda_{tool_name}.zip"
    zip_path = BUILD_DIR / zip_filename
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.write(lambda_file, lambda_file.name)
    return zip_path


def ensure_lambda_role(iam, role_name: str) -> str:
    trust_policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Principal": {"Service": "lambda.amazonaws.com"},
                "Action": "sts:AssumeRole",
            }
        ],
    }

    try:
        role = iam.get_role(RoleName=role_name)["Role"]
        print(f"Using existing Lambda role: {role['Arn']}")
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") != "NoSuchEntity":
            raise

        role = iam.create_role(
            RoleName=role_name,
            AssumeRolePolicyDocument=json.dumps(trust_policy),
            Description="Execution role for AgentCore Gateway Lambda target.",
        )["Role"]
        print(f"Created Lambda role: {role['Arn']}")

    iam.put_role_policy(
        RoleName=role_name,
        PolicyName="AgentCoreGatewayLambdaLogs",
        PolicyDocument=json.dumps(
            {
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
        ),
    )
    iam.get_waiter("role_exists").wait(RoleName=role_name)
    time.sleep(10)
    return role["Arn"]


def ensure_gateway_role(iam, role_name: str, account_id: str, region: str) -> str:
    trust_policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Principal": {"Service": "bedrock-agentcore.amazonaws.com"},
                "Action": "sts:AssumeRole",
                "Condition": {
                    "StringEquals": {"aws:SourceAccount": account_id},
                    "ArnLike": {
                        "aws:SourceArn": f"arn:aws:bedrock-agentcore:{region}:{account_id}:*"
                    },
                },
            }
        ],
    }

    try:
        role = iam.get_role(RoleName=role_name)["Role"]
        print(f"Using existing Gateway role: {role['Arn']}")
        iam.update_assume_role_policy(
            RoleName=role_name,
            PolicyDocument=json.dumps(trust_policy),
        )
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") != "NoSuchEntity":
            raise

        role = iam.create_role(
            RoleName=role_name,
            AssumeRolePolicyDocument=json.dumps(trust_policy),
            Description="Execution role for AgentCore Gateway Lambda target invocation.",
        )["Role"]
        print(f"Created Gateway role: {role['Arn']}")

    iam.get_waiter("role_exists").wait(RoleName=role_name)
    time.sleep(10)
    return role["Arn"]


def ensure_lambda_function(lambda_client, tool_name: str, lambda_file: Path, name: str, role_arn: str) -> str:
    zip_path = create_lambda_zip(tool_name, lambda_file)
    code_bytes = zip_path.read_bytes()
    handler_module = lambda_file.stem

    try:
        response = lambda_client.get_function(FunctionName=name)
        function_arn = response["Configuration"]["FunctionArn"]
        lambda_client.update_function_code(FunctionName=name, ZipFile=code_bytes)
        print(f"Updated Lambda function for {tool_name}: {function_arn}")
        return function_arn
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") != "ResourceNotFoundException":
            raise

    response = lambda_client.create_function(
        FunctionName=name,
        Runtime="python3.13",
        Role=role_arn,
        Handler=f"{handler_module}.lambda_handler",
        Code={"ZipFile": code_bytes},
        Description=f"AgentCore Gateway target for {tool_name}.",
        Timeout=30,
        MemorySize=128,
        Publish=True,
    )
    function_arn = response["FunctionArn"]
    print(f"Created Lambda function for {tool_name}: {function_arn}")
    return function_arn


def ensure_lambda_invoke_permission(
    lambda_client,
    function_name: str,
    principal_arn: str,
    source_arn: str | None = None,
) -> None:
    statement_id = f"AllowAgentCoreGatewayInvoke-{function_name}"
    kwargs = {
        "FunctionName": function_name,
        "StatementId": statement_id,
        "Action": "lambda:InvokeFunction",
        "Principal": principal_arn,
    }
    if source_arn:
        kwargs["SourceArn"] = source_arn

    try:
        lambda_client.add_permission(**kwargs)
        print(f"Added Lambda resource policy permission for {principal_arn}")
    except ClientError as exc:
        error_code = exc.response.get("Error", {}).get("Code")
        if error_code == "InvalidParameterValueException" and source_arn:
            print(
                "SourceArn value not supported for this Lambda permission API; retrying without SourceArn."
            )
            kwargs.pop("SourceArn", None)
            try:
                lambda_client.add_permission(**kwargs)
                print(f"Added Lambda resource policy permission for {principal_arn} without SourceArn")
                return
            except ClientError as exc2:
                if exc2.response.get("Error", {}).get("Code") != "ResourceConflictException":
                    raise
        if error_code != "ResourceConflictException":
            raise
        print("Lambda invoke permission already exists")


def create_gateway(control, name: str, role_arn: str) -> dict:
    try:
        response = control.create_gateway(
            name=name,
            description="Prompt 3 platform-mediated Gateway tools boundary.",
            roleArn=role_arn,
            protocolType="MCP",
            protocolConfiguration={
                "mcp": {
                    "supportedVersions": ["2025-06-18", "2025-03-26"],
                    "instructions": "Expose order tools through AgentCore Gateway.",
                }
            },
            authorizerType=os.getenv("AGENTCORE_GATEWAY_AUTHORIZER", "AWS_IAM"),
            exceptionLevel="DEBUG",
            tags={"ArchitecturePattern": "AgentCoreGatewayToolsBoundary"},
        )
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") != "ConflictException":
            raise
        raise RuntimeError(
            f"Gateway {name} already exists. Set AGENTCORE_GATEWAY_ID and "
            "AGENTCORE_GATEWAY_URL from the existing gateway, or choose a new "
            "AGENTCORE_GATEWAY_NAME."
        ) from exc

    print("Gateway create response:")
    print(response)
    return response


def wait_for_gateway_ready(control, gateway_id: str) -> dict:
    while True:
        response = control.get_gateway(gatewayIdentifier=gateway_id)
        status = response["status"]
        print(f"Gateway status: {status}")

        if status == "READY":
            return response

        if status in {"CREATE_FAILED", "UPDATE_FAILED", "DELETING"}:
            raise RuntimeError(f"Gateway did not become READY: {response}")

        time.sleep(10)


def create_lambda_target(control, gateway_id: str, tool_name: str, tool_description: str, lambda_arn: str) -> dict:
    all_schemas = json.loads(TOOL_SCHEMA_PATH.read_text(encoding="utf-8"))
    tool_schema = next((s for s in all_schemas if s["name"] == tool_name), None)
    if not tool_schema:
        raise RuntimeError(f"Tool schema not found for {tool_name}")

    target_name = f"{tool_name.replace('_', '')}Target"
    response = control.create_gateway_target(
        gatewayIdentifier=gateway_id,
        name=target_name,
        description=tool_description,
        targetConfiguration={
            "mcp": {
                "lambda": {
                    "lambdaArn": lambda_arn,
                    "toolSchema": {"inlinePayload": [tool_schema]},
                }
            }
        },
        credentialProviderConfigurations=[
            {"credentialProviderType": "GATEWAY_IAM_ROLE"}
        ],
    )
    print(f"Gateway target create response for {tool_name}:")
    print(response)
    return response


def main() -> None:
    parser = argparse.ArgumentParser(description="Create AgentCore Gateway and two Lambda targets (one per tool).")
    parser.add_argument("--skip-targets", action="store_true", help="Create Gateway only.")
    args = parser.parse_args()

    region = os.getenv("AWS_REGION", "eu-west-2")
    gateway_name = validate_name(
        os.getenv("AGENTCORE_GATEWAY_NAME", DEFAULT_GATEWAY_NAME),
        "AGENTCORE_GATEWAY_NAME",
        GATEWAY_NAME_PATTERN,
        "([0-9a-zA-Z][-]?){1,48}",
    )

    account_id = get_account_id()
    iam = boto3.client("iam")
    lambda_client = boto3.client("lambda", region_name=region)
    control = boto3.client("bedrock-agentcore-control", region_name=region)

    # Collect all Lambda ARNs for the Gateway role
    lambda_arns = []
    lambda_names = []  # Track Lambda names for permission assignment

    # Create Lambda functions and roles for each tool
    for tool in TOOLS:
        tool_name = tool["name"]
        lambda_file = tool["lambda_file"]
        default_lambda_name = tool["default_lambda_name"]
        env_var_name = f"AGENTCORE_GATEWAY_LAMBDA_NAME_{tool_name.upper()}"
        lambda_name = validate_name(
            os.getenv(env_var_name, default_lambda_name),
            env_var_name,
            LAMBDA_NAME_PATTERN,
            "[a-zA-Z0-9-_]{1,64}",
        )

        lambda_role_arn = ensure_lambda_role(iam, lambda_role_name(region, tool_name))
        lambda_arn = ensure_lambda_function(lambda_client, tool_name, lambda_file, lambda_name, lambda_role_arn)
        lambda_arns.append(lambda_arn)
        lambda_names.append(lambda_name)

    gateway_role_arn = ensure_gateway_role(
        iam,
        gateway_role_name(region),
        account_id,
        region,
    )

    gateway = create_gateway(control, gateway_name, gateway_role_arn)
    gateway_id = gateway["gatewayId"]
    gateway_arn = gateway["gatewayArn"]
    gateway_url = gateway["gatewayUrl"]
    wait_for_gateway_ready(control, gateway_id)

    iam.put_role_policy(
        RoleName=gateway_role_name(region),
        PolicyName="AgentCoreGatewayInvokeAllLambdaTargets",
        PolicyDocument=json.dumps(
            {
                "Version": "2012-10-17",
                "Statement": [
                    {
                        "Effect": "Allow",
                        "Action": ["lambda:InvokeFunction"],
                        "Resource": lambda_arns,
                    },
                    {
                        "Effect": "Allow",
                        "Action": ["bedrock-agentcore:InvokeGateway"],
                        "Resource": [gateway_arn],
                    },
                ],
            }
        ),
    )

    # Add Gateway invoke permission to each Lambda, scoped to the created Gateway ARN.
    for lambda_name, lambda_arn in zip(lambda_names, lambda_arns):
        ensure_lambda_invoke_permission(
            lambda_client,
            lambda_name,
            gateway_role_arn,
            source_arn=gateway_arn,
        )

    targets = []
    if not args.skip_targets:
        for tool, lambda_arn in zip(TOOLS, lambda_arns):
            target = create_lambda_target(
                control,
                gateway_id,
                tool["name"],
                tool["description"],
                lambda_arn,
            )
            targets.append(target)

    print(f"AGENTCORE_GATEWAY_ID={gateway_id}")
    print(f"AGENTCORE_GATEWAY_ARN={gateway_arn}")
    print(f"AGENTCORE_GATEWAY_URL={gateway_url}")
    for tool, lambda_arn in zip(TOOLS, lambda_arns):
        print(f"AGENTCORE_GATEWAY_LAMBDA_ARN_{tool['name'].upper()}={lambda_arn}")
    for target in targets:
        print(f"AGENTCORE_GATEWAY_TARGET_ID_{target.get('targetConfiguration', {}).get('mcp', {}).get('lambda', {}).get('lambdaArn', '').split(':')[-1]}={target['targetId']}")


if __name__ == "__main__":
    main()
