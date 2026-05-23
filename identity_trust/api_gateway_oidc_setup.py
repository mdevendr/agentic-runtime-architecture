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
LAMBDA_SOURCE = BASE_DIR / "lambda_oidc_runtime_app.py"


def require_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"{name} is required")
    return value


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


def ensure_role(iam, role_name: str) -> str:
    try:
        role = iam.get_role(RoleName=role_name)["Role"]
        iam.update_assume_role_policy(
            RoleName=role_name,
            PolicyDocument=json.dumps(lambda_trust_policy()),
        )
        print(f"Using existing OIDC API Lambda role: {role['Arn']}")
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") != "NoSuchEntity":
            raise
        role = iam.create_role(
            RoleName=role_name,
            AssumeRolePolicyDocument=json.dumps(lambda_trust_policy()),
            Description="Prompt 4 OIDC API callback Lambda role.",
            Tags=[{"Key": "ArchitecturePattern", "Value": "Prompt4IdentityTrust"}],
        )["Role"]
        print(f"Created OIDC API Lambda role: {role['Arn']}")

    iam.put_role_policy(
        RoleName=role_name,
        PolicyName="Prompt4OidcApiLambdaLogs",
        PolicyDocument=json.dumps(logs_policy()),
    )
    iam.get_waiter("role_exists").wait(RoleName=role_name)
    return role["Arn"]


def create_lambda_zip() -> Path:
    BUILD_DIR.mkdir(exist_ok=True)
    zip_path = BUILD_DIR / "lambda_oidc_runtime_app.zip"
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.write(LAMBDA_SOURCE, "lambda_oidc_runtime_app.py")
    return zip_path


def ensure_api(apigw, api_name: str) -> dict[str, str]:
    apis = apigw.get_apis()["Items"]
    for api in apis:
        if api["Name"] == api_name:
            print(f"Using existing HTTP API: {api['ApiEndpoint']}")
            return {"api_id": api["ApiId"], "api_endpoint": api["ApiEndpoint"]}

    api = apigw.create_api(
        Name=api_name,
        ProtocolType="HTTP",
        Description="Prompt 4 Cognito/Google OIDC callback API.",
        Tags={"ArchitecturePattern": "Prompt4IdentityTrust"},
    )
    print(f"Created HTTP API: {api['ApiEndpoint']}")
    return {"api_id": api["ApiId"], "api_endpoint": api["ApiEndpoint"]}


def ensure_stage(apigw, api_id: str, stage_name: str) -> None:
    try:
        apigw.get_stage(ApiId=api_id, StageName=stage_name)
        apigw.update_stage(ApiId=api_id, StageName=stage_name, AutoDeploy=True)
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") != "NotFoundException":
            raise
        apigw.create_stage(ApiId=api_id, StageName=stage_name, AutoDeploy=True)


def ensure_function(
    lambda_client,
    function_name: str,
    role_arn: str,
    env: dict[str, str],
    zip_path: Path,
) -> str:
    with zip_path.open("rb") as package:
        code_bytes = package.read()

    try:
        function = lambda_client.get_function(FunctionName=function_name)["Configuration"]
        lambda_client.update_function_code(
            FunctionName=function_name,
            ZipFile=code_bytes,
            Publish=True,
        )
        waiter = lambda_client.get_waiter("function_updated")
        waiter.wait(FunctionName=function_name)
        lambda_client.update_function_configuration(
            FunctionName=function_name,
            Role=role_arn,
            Handler="lambda_oidc_runtime_app.handler",
            Runtime="python3.12",
            Timeout=120,
            Environment={"Variables": env},
        )
        waiter.wait(FunctionName=function_name)
        print(f"Using existing OIDC API Lambda function: {function['FunctionArn']}")
        return function["FunctionArn"]
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") != "ResourceNotFoundException":
            raise

    function = lambda_client.create_function(
        FunctionName=function_name,
        Runtime="python3.12",
        Role=role_arn,
        Handler="lambda_oidc_runtime_app.handler",
        Code={"ZipFile": code_bytes},
        Timeout=120,
        Environment={"Variables": env},
        Tags={"ArchitecturePattern": "Prompt4IdentityTrust"},
        Publish=True,
    )
    lambda_client.get_waiter("function_active").wait(FunctionName=function_name)
    print(f"Created OIDC API Lambda function: {function['FunctionArn']}")
    return function["FunctionArn"]


def ensure_integration(apigw, api_id: str, function_arn: str) -> str:
    integrations = apigw.get_integrations(ApiId=api_id)["Items"]
    for integration in integrations:
        if integration.get("IntegrationUri") == function_arn:
            return integration["IntegrationId"]

    integration = apigw.create_integration(
        ApiId=api_id,
        IntegrationType="AWS_PROXY",
        IntegrationUri=function_arn,
        PayloadFormatVersion="2.0",
    )
    return integration["IntegrationId"]


def ensure_route(apigw, api_id: str, route_key: str, integration_id: str) -> None:
    routes = apigw.get_routes(ApiId=api_id)["Items"]
    target = f"integrations/{integration_id}"
    for route in routes:
        if route["RouteKey"] == route_key:
            apigw.update_route(ApiId=api_id, RouteId=route["RouteId"], Target=target)
            return
    apigw.create_route(ApiId=api_id, RouteKey=route_key, Target=target)


def ensure_lambda_permission(lambda_client, function_name: str, account_id: str, region: str, api_id: str) -> None:
    statement_id = "Prompt4OidcApiGatewayInvoke"
    source_arn = f"arn:aws:execute-api:{region}:{account_id}:{api_id}/*/*/*"
    try:
        lambda_client.add_permission(
            FunctionName=function_name,
            StatementId=statement_id,
            Action="lambda:InvokeFunction",
            Principal="apigateway.amazonaws.com",
            SourceArn=source_arn,
        )
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") != "ResourceConflictException":
            raise


def update_user_client_callbacks(
    cognito,
    user_pool_id: str,
    client_id: str,
    callback_url: str,
    logout_url: str,
    scope: str,
) -> None:
    client = cognito.describe_user_pool_client(
        UserPoolId=user_pool_id,
        ClientId=client_id,
    )["UserPoolClient"]
    callback_urls = sorted(set(client.get("CallbackURLs", []) + [callback_url]))
    logout_urls = sorted(set(client.get("LogoutURLs", []) + [logout_url]))
    providers = client.get("SupportedIdentityProviders", ["COGNITO"])
    scopes = sorted(set(client.get("AllowedOAuthScopes", []) + ["openid", "email", "profile", scope]))

    cognito.update_user_pool_client(
        UserPoolId=user_pool_id,
        ClientId=client_id,
        ClientName=client["ClientName"],
        ExplicitAuthFlows=client.get("ExplicitAuthFlows", []),
        SupportedIdentityProviders=providers,
        CallbackURLs=callback_urls,
        LogoutURLs=logout_urls,
        AllowedOAuthFlows=["code"],
        AllowedOAuthScopes=scopes,
        AllowedOAuthFlowsUserPoolClient=True,
        PreventUserExistenceErrors=client.get("PreventUserExistenceErrors", "ENABLED"),
    )


def write_env_file(path: Path, values: dict[str, str]) -> None:
    lines = [f"{key}={value}" for key, value in values.items()]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Create API Gateway/Lambda callback boundary for Prompt 4 scenario 6."
    )
    parser.add_argument("--profile", help="AWS profile to use for setup.")
    parser.add_argument("--region", default=os.getenv("AWS_REGION", "eu-west-2"))
    parser.add_argument("--runtime-arn", default=os.getenv("AGENT_RUNTIME_ARN_OIDC_JWT"))
    parser.add_argument("--cognito-user-pool-id", default=os.getenv("PROMPT4_COGNITO_USER_POOL_ID"))
    parser.add_argument("--cognito-user-client-id", default=os.getenv("PROMPT4_COGNITO_USER_CLIENT_ID"))
    parser.add_argument("--cognito-hosted-ui-base-url", default=os.getenv("PROMPT4_COGNITO_HOSTED_UI_BASE_URL"))
    parser.add_argument("--cognito-token-url", default=os.getenv("PROMPT4_COGNITO_TOKEN_URL"))
    parser.add_argument("--scope", default=os.getenv("PROMPT4_COGNITO_SCOPE", "agentcore-runtime/invoke"))
    parser.add_argument("--identity-provider", default=os.getenv("PROMPT4_COGNITO_GOOGLE_PROVIDER", "Google"))
    parser.add_argument("--api-name", default=os.getenv("PROMPT4_OIDC_API_NAME", "prompt4-oidc-runtime-api"))
    parser.add_argument("--stage-name", default=os.getenv("PROMPT4_OIDC_API_STAGE", "$default"))
    parser.add_argument("--function-name", default=os.getenv("PROMPT4_OIDC_LAMBDA_NAME", "prompt4-oidc-runtime-callback"))
    parser.add_argument("--role-name", default=os.getenv("PROMPT4_OIDC_LAMBDA_ROLE_NAME", "Prompt4OidcRuntimeCallbackRole"))
    parser.add_argument("--env-file", default=str(BASE_DIR / "api_gateway_oidc.env"))
    args = parser.parse_args()

    runtime_arn = args.runtime_arn or require_env("AGENT_RUNTIME_ARN_OIDC_JWT")
    user_pool_id = args.cognito_user_pool_id or require_env("PROMPT4_COGNITO_USER_POOL_ID")
    user_client_id = args.cognito_user_client_id or require_env("PROMPT4_COGNITO_USER_CLIENT_ID")
    hosted_ui_base = args.cognito_hosted_ui_base_url or require_env("PROMPT4_COGNITO_HOSTED_UI_BASE_URL")
    token_url = args.cognito_token_url or require_env("PROMPT4_COGNITO_TOKEN_URL")

    session = boto3.Session(profile_name=args.profile) if args.profile else boto3.Session()
    account_id = session.client("sts").get_caller_identity()["Account"]
    iam = session.client("iam")
    lambda_client = session.client("lambda", region_name=args.region)
    apigw = session.client("apigatewayv2", region_name=args.region)
    cognito = session.client("cognito-idp", region_name=args.region)

    api = ensure_api(apigw, args.api_name)
    ensure_stage(apigw, api["api_id"], args.stage_name)
    callback_url = f"{api['api_endpoint']}/callback"
    start_url = f"{api['api_endpoint']}/start"
    logout_url = f"{api['api_endpoint']}/logout"

    role_arn = ensure_role(iam, args.role_name)
    time.sleep(10)
    env = {
        "AGENT_RUNTIME_ARN_OIDC_JWT": runtime_arn,
        "PROMPT4_COGNITO_HOSTED_UI_BASE_URL": hosted_ui_base,
        "PROMPT4_COGNITO_TOKEN_URL": token_url,
        "PROMPT4_COGNITO_USER_CLIENT_ID": user_client_id,
        "PROMPT4_COGNITO_USER_SCOPES": f"openid email profile {args.scope}",
        "PROMPT4_COGNITO_IDENTITY_PROVIDER": args.identity_provider,
        "PROMPT4_OIDC_CALLBACK_URL": callback_url,
    }
    function_arn = ensure_function(
        lambda_client,
        args.function_name,
        role_arn,
        env,
        create_lambda_zip(),
    )
    integration_id = ensure_integration(apigw, api["api_id"], function_arn)
    ensure_route(apigw, api["api_id"], "GET /start", integration_id)
    ensure_route(apigw, api["api_id"], "GET /callback", integration_id)
    ensure_lambda_permission(lambda_client, args.function_name, account_id, args.region, api["api_id"])
    update_user_client_callbacks(cognito, user_pool_id, user_client_id, callback_url, logout_url, args.scope)

    hosted_login_url = (
        f"{hosted_ui_base}/oauth2/authorize?"
        f"response_type=code&client_id={user_client_id}"
        f"&redirect_uri={callback_url}"
        f"&scope=openid%20email%20profile%20{args.scope}"
        f"&identity_provider={args.identity_provider}"
    )
    values = {
        "PROMPT4_OIDC_API_ID": api["api_id"],
        "PROMPT4_OIDC_API_ENDPOINT": api["api_endpoint"],
        "PROMPT4_OIDC_START_URL": start_url,
        "PROMPT4_OIDC_CALLBACK_URL": callback_url,
        "PROMPT4_OIDC_LOGIN_URL": hosted_login_url,
        "PROMPT4_OIDC_LAMBDA_NAME": args.function_name,
        "PROMPT4_OIDC_LAMBDA_ARN": function_arn,
        "PROMPT4_OIDC_LAMBDA_ROLE_ARN": role_arn,
    }
    write_env_file(Path(args.env_file), values)
    for key, value in values.items():
        print(f"export {key}={value}")


if __name__ == "__main__":
    main()
