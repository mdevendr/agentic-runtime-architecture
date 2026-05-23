import argparse
import asyncio
import base64
from contextlib import redirect_stderr, redirect_stdout
import json
import os
import re
import sys
import uuid
from contextlib import AsyncExitStack
from datetime import datetime, timezone
from io import StringIO
from pathlib import Path
from typing import Any
from urllib.parse import quote

import boto3
import httpx
from botocore.exceptions import ClientError
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from main import AWS_REGION, BedrockAgentCoreSigV4Auth


BASE_DIR = Path(__file__).parent
DEFAULT_EVIDENCE_DIR = BASE_DIR / "evidence"
JWT_RE = re.compile(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b")
BEARER_RE = re.compile(r"Bearer\s+[A-Za-z0-9._~+/=-]+", re.IGNORECASE)
SENSITIVE_JSON_RE = re.compile(
    r'("(?:access_token|id_token|refresh_token|client_secret|secret_access_key|session_token|authorization_code|code)"\s*:\s*")([^"]+)(")',
    re.IGNORECASE,
)
SENSITIVE_ENV_RE = re.compile(
    r"((?:CLIENT_SECRET|SECRET_ACCESS_KEY|SESSION_TOKEN|ACCESS_TOKEN|ID_TOKEN|REFRESH_TOKEN|AUTHORIZATION_CODE|PASSWORD)=)([^\s]+)",
    re.IGNORECASE,
)


def boto3_session(
    profile: str | None = None,
    role_arn: str | None = None,
    access_key_id: str | None = None,
    secret_access_key: str | None = None,
    session_token: str | None = None,
    session_name: str = "prompt4-identity-test",
) -> boto3.Session:
    if access_key_id or secret_access_key or session_token:
        if not access_key_id or not secret_access_key:
            raise RuntimeError(
                "Both access key ID and secret access key are required for IAM user tests"
            )
        return boto3.Session(
            aws_access_key_id=access_key_id,
            aws_secret_access_key=secret_access_key,
            aws_session_token=session_token,
            region_name=AWS_REGION,
        )

    base_session = boto3.Session(profile_name=profile) if profile else boto3.Session()
    if not role_arn:
        return base_session

    sts = base_session.client("sts", region_name=AWS_REGION)
    credentials = sts.assume_role(
        RoleArn=role_arn,
        RoleSessionName=session_name,
    )["Credentials"]
    return boto3.Session(
        aws_access_key_id=credentials["AccessKeyId"],
        aws_secret_access_key=credentials["SecretAccessKey"],
        aws_session_token=credentials["SessionToken"],
        region_name=AWS_REGION,
    )


def runtime_client(
    profile: str | None = None,
    role_arn: str | None = None,
    access_key_id: str | None = None,
    secret_access_key: str | None = None,
    session_token: str | None = None,
):
    session = boto3_session(
        profile=profile,
        role_arn=role_arn,
        access_key_id=access_key_id,
        secret_access_key=secret_access_key,
        session_token=session_token,
    )
    return session.client("bedrock-agentcore", region_name=AWS_REGION)


def read_response(response: dict[str, Any]) -> str:
    chunks = []
    for chunk in response.get("response", []):
        chunks.append(chunk.decode("utf-8"))
    return "".join(chunks)


def redact_sensitive_text(text: str) -> str:
    text = JWT_RE.sub("REDACTED_JWT", text)
    text = BEARER_RE.sub("Bearer REDACTED_TOKEN", text)
    text = SENSITIVE_JSON_RE.sub(r"\1REDACTED\3", text)
    text = SENSITIVE_ENV_RE.sub(r"\1REDACTED", text)
    return text


def redact_sensitive_obj(value: Any) -> Any:
    if isinstance(value, dict):
        redacted = {}
        for key, item in value.items():
            key_text = str(key).lower()
            if any(
                marker in key_text
                for marker in (
                    "access_token",
                    "id_token",
                    "refresh_token",
                    "client_secret",
                    "secret_access_key",
                    "session_token",
                    "authorization",
                    "password",
                    "authorization_code",
                )
            ):
                redacted[key] = "REDACTED"
            else:
                redacted[key] = redact_sensitive_obj(item)
        return redacted
    if isinstance(value, list):
        return [redact_sensitive_obj(item) for item in value]
    if isinstance(value, str):
        return redact_sensitive_text(value)
    return value


def oauth_token() -> str:
    token_url = os.getenv("PROMPT4_COGNITO_TOKEN_URL")
    client_id = os.getenv("PROMPT4_COGNITO_MACHINE_CLIENT_ID")
    client_secret = os.getenv("PROMPT4_COGNITO_MACHINE_CLIENT_SECRET")
    scope = os.getenv("PROMPT4_COGNITO_SCOPE", "agentcore-runtime/invoke")
    if not token_url or not client_id or not client_secret:
        raise RuntimeError(
            "PROMPT4_COGNITO_TOKEN_URL, PROMPT4_COGNITO_MACHINE_CLIENT_ID, "
            "and PROMPT4_COGNITO_MACHINE_CLIENT_SECRET are required"
        )

    response = httpx.post(
        token_url,
        data={"grant_type": "client_credentials", "scope": scope},
        auth=(client_id, client_secret),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=30,
    )
    response.raise_for_status()
    payload = response.json()
    return payload["access_token"]


def external_idp_token() -> str:
    token_url = os.getenv("EXTERNAL_IDP_TOKEN_URL")
    client_id = os.getenv("EXTERNAL_IDP_CLIENT_ID")
    client_secret = os.getenv("EXTERNAL_IDP_CLIENT_SECRET")
    token_scope = os.getenv("EXTERNAL_IDP_TOKEN_SCOPE")
    if not token_scope:
        audiences = [
            item.strip()
            for item in os.getenv("EXTERNAL_IDP_ALLOWED_AUDIENCES", "").split(",")
            if item.strip()
        ]
        if audiences:
            token_scope = f"{audiences[0]}/.default"

    if not token_url or not client_id or not client_secret or not token_scope:
        raise RuntimeError(
            "EXTERNAL_IDP_TOKEN_URL, EXTERNAL_IDP_CLIENT_ID, "
            "EXTERNAL_IDP_CLIENT_SECRET, and EXTERNAL_IDP_TOKEN_SCOPE "
            "or EXTERNAL_IDP_ALLOWED_AUDIENCES are required"
        )

    response = httpx.post(
        token_url,
        data={
            "grant_type": "client_credentials",
            "client_id": client_id,
            "client_secret": client_secret,
            "scope": token_scope,
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=30,
    )
    response.raise_for_status()
    payload = response.json()
    return payload["access_token"]


def decode_jwt_claims(token: str) -> dict[str, Any]:
    parts = token.split(".")
    if len(parts) < 2:
        raise RuntimeError("Token is not a JWT")
    payload = parts[1]
    payload += "=" * (-len(payload) % 4)
    return json.loads(base64.urlsafe_b64decode(payload.encode("utf-8")))


def print_external_idp_claims() -> None:
    token = external_idp_token()
    claims = decode_jwt_claims(token)
    selected = {
        "iss": claims.get("iss"),
        "aud": claims.get("aud"),
        "appid": claims.get("appid"),
        "azp": claims.get("azp"),
        "client_id": claims.get("client_id"),
        "scp": claims.get("scp"),
        "roles": claims.get("roles"),
        "tid": claims.get("tid"),
        "ver": claims.get("ver"),
        "exp": claims.get("exp"),
    }
    print(json.dumps(selected, indent=2))


def invoke_runtime_with_bearer(
    runtime_arn: str,
    token: str | None,
) -> dict[str, Any]:
    region = os.getenv("AWS_REGION", AWS_REGION)
    encoded_runtime_arn = quote(runtime_arn, safe="")
    url = (
        f"https://bedrock-agentcore.{region}.amazonaws.com"
        f"/runtimes/{encoded_runtime_arn}/invocations"
    )
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "X-Amzn-Bedrock-AgentCore-Runtime-Session-Id": f"oauth-client-{uuid.uuid4()}",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"

    payload = {
        "prompt": (
            "Call calculate_order_total with exactly these arguments: "
            "sku is SKU-BOOK-001, quantity is 3, and unit_price is 12.50."
        ),
        "tool_choice": "calculate_order_total",
    }
    response = httpx.post(url, headers=headers, json=payload, timeout=120)
    return {
        "status_code": response.status_code,
        "headers": {
            "content-type": response.headers.get("content-type"),
            "x-amzn-requestid": response.headers.get("x-amzn-requestid"),
        },
        "text": response.text,
    }


def invoke_runtime(
    profile: str | None,
    role_arn: str | None,
    runtime_arn: str,
    access_key_id: str | None = None,
    secret_access_key: str | None = None,
    session_token: str | None = None,
) -> str:
    client = runtime_client(
        profile=profile,
        role_arn=role_arn,
        access_key_id=access_key_id,
        secret_access_key=secret_access_key,
        session_token=session_token,
    )
    response = client.invoke_agent_runtime(
        agentRuntimeArn=runtime_arn,
        runtimeSessionId="identity-test-00000000-0000-4000-8000-000000000001",
        contentType="application/json",
        accept="application/json",
        payload=json.dumps(
            {
                "prompt": (
                    "Call calculate_order_total with exactly these arguments: "
                    "sku is SKU-BOOK-001, quantity is 3, and unit_price is 12.50."
                ),
                "tool_choice": "calculate_order_total",
            }
        ).encode("utf-8"),
    )
    return read_response(response)


def invoke_lambda_workload(
    profile: str | None,
    function_name: str,
    runtime_arn: str,
) -> dict[str, Any]:
    session = boto3.Session(profile_name=profile) if profile else boto3.Session()
    client = session.client("lambda", region_name=AWS_REGION)
    response = client.invoke(
        FunctionName=function_name,
        InvocationType="RequestResponse",
        LogType="Tail",
        Payload=json.dumps({"runtime_arn": runtime_arn}).encode("utf-8"),
    )
    payload_text = response["Payload"].read().decode("utf-8")
    payload = json.loads(payload_text) if payload_text else {}
    result = {
        "status_code": response.get("StatusCode"),
        "function_error": response.get("FunctionError"),
        "payload": payload,
    }
    if response.get("LogResult"):
        result["log_tail"] = base64.b64decode(response["LogResult"]).decode(
            "utf-8",
            errors="replace",
        )
    return result


async def gateway_tools_list(use_sigv4: bool) -> list[str]:
    gateway_url = os.getenv("AGENTCORE_GATEWAY_URL")
    if not gateway_url:
        raise RuntimeError("AGENTCORE_GATEWAY_URL is required")

    auth = BedrockAgentCoreSigV4Auth(AWS_REGION) if use_sigv4 else None
    async with AsyncExitStack() as stack:
        http_client = httpx.AsyncClient(auth=auth, timeout=httpx.Timeout(30))
        await stack.enter_async_context(http_client)
        read, write, _get_session_id = await stack.enter_async_context(
            streamable_http_client(gateway_url, http_client=http_client)
        )
        session = await stack.enter_async_context(ClientSession(read, write))
        await session.initialize()
        response = await session.list_tools()
        return [tool.name for tool in response.tools]


def expect_runtime_authorized(args: argparse.Namespace) -> None:
    runtime_arn = args.runtime_arn or os.getenv("AGENT_RUNTIME_ARN")
    if not runtime_arn:
        raise RuntimeError("AGENT_RUNTIME_ARN or --runtime-arn is required")
    response_text = invoke_runtime(
        args.profile,
        args.role_arn,
        runtime_arn,
        access_key_id=args.access_key_id,
        secret_access_key=args.secret_access_key,
        session_token=args.session_token,
    )
    print(response_text)
    print("Authorized runtime invocation succeeded", file=sys.stderr)


def expect_runtime_unauthorized(args: argparse.Namespace) -> None:
    runtime_arn = args.runtime_arn or os.getenv("AGENT_RUNTIME_ARN")
    if not runtime_arn:
        raise RuntimeError("AGENT_RUNTIME_ARN or --runtime-arn is required")
    try:
        invoke_runtime(
            args.profile,
            args.role_arn,
            runtime_arn,
            access_key_id=args.access_key_id,
            secret_access_key=args.secret_access_key,
            session_token=args.session_token,
        )
    except ClientError as exc:
        print("Unauthorized runtime invocation failed as expected", file=sys.stderr)
        print(exc, file=sys.stderr)
        return
    raise RuntimeError("Expected unauthorized runtime invocation to fail, but it succeeded")


def expect_lambda_workload_authorized(args: argparse.Namespace) -> None:
    runtime_arn = args.runtime_arn or os.getenv("AGENT_RUNTIME_ARN_IAM") or os.getenv("AGENT_RUNTIME_ARN")
    if not runtime_arn:
        raise RuntimeError("AGENT_RUNTIME_ARN_IAM, AGENT_RUNTIME_ARN, or --runtime-arn is required")
    if not args.function_name:
        raise RuntimeError("Lambda workload function name is required")

    result = invoke_lambda_workload(args.profile, args.function_name, runtime_arn)
    print(json.dumps(result, indent=2))
    if result.get("function_error"):
        raise RuntimeError(f"Lambda function failed: {result['function_error']}")
    payload = result.get("payload", {})
    if not payload.get("ok"):
        raise RuntimeError(f"Expected Lambda workload invocation to succeed: {payload}")
    print("Authorized Lambda workload invocation succeeded", file=sys.stderr)


def expect_lambda_workload_unauthorized(args: argparse.Namespace) -> None:
    runtime_arn = args.runtime_arn or os.getenv("AGENT_RUNTIME_ARN_IAM") or os.getenv("AGENT_RUNTIME_ARN")
    if not runtime_arn:
        raise RuntimeError("AGENT_RUNTIME_ARN_IAM, AGENT_RUNTIME_ARN, or --runtime-arn is required")
    if not args.function_name:
        raise RuntimeError("Lambda workload function name is required")

    result = invoke_lambda_workload(args.profile, args.function_name, runtime_arn)
    print(json.dumps(result, indent=2))
    payload = result.get("payload", {})
    if payload.get("ok"):
        raise RuntimeError("Expected Lambda workload invocation to fail, but it succeeded")
    if payload.get("error_code") != "AccessDeniedException":
        raise RuntimeError(
            "Expected Lambda workload to fail with AccessDeniedException, "
            f"got {payload.get('error_code')}"
        )
    print("Unauthorized Lambda workload invocation failed as expected", file=sys.stderr)


def expect_oauth_client_authorized(args: argparse.Namespace) -> None:
    runtime_arn = args.runtime_arn or os.getenv("AGENT_RUNTIME_ARN_OAUTH_CLIENT")
    if not runtime_arn:
        raise RuntimeError("AGENT_RUNTIME_ARN_OAUTH_CLIENT or --runtime-arn is required")

    token = oauth_token()
    result = invoke_runtime_with_bearer(runtime_arn, token)
    print(json.dumps(result, indent=2))
    if result["status_code"] < 200 or result["status_code"] >= 300:
        raise RuntimeError(f"Expected OAuth Runtime invocation to succeed: {result}")
    print("Authorized OAuth client Runtime invocation succeeded", file=sys.stderr)


def expect_oauth_client_missing_token(args: argparse.Namespace) -> None:
    runtime_arn = args.runtime_arn or os.getenv("AGENT_RUNTIME_ARN_OAUTH_CLIENT")
    if not runtime_arn:
        raise RuntimeError("AGENT_RUNTIME_ARN_OAUTH_CLIENT or --runtime-arn is required")

    result = invoke_runtime_with_bearer(runtime_arn, None)
    print(json.dumps(result, indent=2))
    if result["status_code"] < 400:
        raise RuntimeError("Expected missing-token Runtime invocation to fail")
    print("Missing-token OAuth Runtime invocation failed as expected", file=sys.stderr)


def expect_external_jwt_authorized(args: argparse.Namespace) -> None:
    runtime_arn = args.runtime_arn or os.getenv("AGENT_RUNTIME_ARN_EXTERNAL_JWT")
    if not runtime_arn:
        raise RuntimeError("AGENT_RUNTIME_ARN_EXTERNAL_JWT or --runtime-arn is required")

    token = external_idp_token()
    result = invoke_runtime_with_bearer(runtime_arn, token)
    print(json.dumps(result, indent=2))
    if result["status_code"] < 200 or result["status_code"] >= 300:
        raise RuntimeError(f"Expected external JWT Runtime invocation to succeed: {result}")
    print("Authorized external JWT Runtime invocation succeeded", file=sys.stderr)


def expect_external_jwt_missing_token(args: argparse.Namespace) -> None:
    runtime_arn = args.runtime_arn or os.getenv("AGENT_RUNTIME_ARN_EXTERNAL_JWT")
    if not runtime_arn:
        raise RuntimeError("AGENT_RUNTIME_ARN_EXTERNAL_JWT or --runtime-arn is required")

    result = invoke_runtime_with_bearer(runtime_arn, None)
    print(json.dumps(result, indent=2))
    if result["status_code"] < 400:
        raise RuntimeError("Expected missing-token external JWT Runtime invocation to fail")
    print("Missing-token external JWT Runtime invocation failed as expected", file=sys.stderr)


def expect_gateway_authorized() -> None:
    tools = asyncio.run(gateway_tools_list(use_sigv4=True))
    print(json.dumps({"tools": tools}, indent=2))
    print("Authorized Gateway tools/list succeeded", file=sys.stderr)


def expect_gateway_unauthorized() -> None:
    try:
        asyncio.run(gateway_tools_list(use_sigv4=False))
    except Exception as exc:
        print("Unauthorized Gateway tools/list failed as expected", file=sys.stderr)
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return
    raise RuntimeError("Expected unsigned Gateway tools/list to fail, but it succeeded")


def load_scenario(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Scenario file is not valid JSON: {path}") from exc


def apply_env(overrides: dict[str, str]) -> dict[str, str | None]:
    previous = {key: os.getenv(key) for key in overrides}
    for key, value in overrides.items():
        os.environ[key] = value
    return previous


def restore_env(previous: dict[str, str | None]) -> None:
    for key, value in previous.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value


def evidence_path(base_dir: Path, scenario_name: str) -> Path:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = base_dir / scenario_name / timestamp
    path.mkdir(parents=True, exist_ok=True)
    return path


def scenario_args(test: dict[str, Any]) -> argparse.Namespace:
    profile = test.get("profile")
    profile_env = test.get("profile_env")
    if profile_env:
        profile = os.getenv(profile_env)

    runtime_arn = test.get("runtime_arn")
    runtime_arn_env = test.get("runtime_arn_env")
    if runtime_arn_env:
        runtime_arn = os.getenv(runtime_arn_env)

    role_arn = test.get("role_arn")
    role_arn_env = test.get("role_arn_env")
    if role_arn_env:
        role_arn = os.getenv(role_arn_env)

    function_name = test.get("function_name")
    function_name_env = test.get("function_name_env")
    if function_name_env:
        function_name = os.getenv(function_name_env)

    access_key_id = test.get("access_key_id")
    access_key_id_env = test.get("access_key_id_env")
    if access_key_id_env:
        access_key_id = os.getenv(access_key_id_env)

    secret_access_key = test.get("secret_access_key")
    secret_access_key_env = test.get("secret_access_key_env")
    if secret_access_key_env:
        secret_access_key = os.getenv(secret_access_key_env)

    session_token = test.get("session_token")
    session_token_env = test.get("session_token_env")
    if session_token_env:
        session_token = os.getenv(session_token_env)

    return argparse.Namespace(
        profile=profile,
        role_arn=role_arn,
        runtime_arn=runtime_arn,
        function_name=function_name,
        access_key_id=access_key_id,
        secret_access_key=secret_access_key,
        session_token=session_token,
    )


def run_named_test(mode: str, args: argparse.Namespace) -> None:
    if mode == "runtime-authorized":
        expect_runtime_authorized(args)
    elif mode == "runtime-unauthorized":
        expect_runtime_unauthorized(args)
    elif mode == "gateway-authorized":
        expect_gateway_authorized()
    elif mode == "gateway-unauthorized":
        expect_gateway_unauthorized()
    elif mode == "lambda-workload-authorized":
        expect_lambda_workload_authorized(args)
    elif mode == "lambda-workload-unauthorized":
        expect_lambda_workload_unauthorized(args)
    elif mode == "oauth-client-authorized":
        expect_oauth_client_authorized(args)
    elif mode == "oauth-client-missing-token":
        expect_oauth_client_missing_token(args)
    elif mode == "external-jwt-authorized":
        expect_external_jwt_authorized(args)
    elif mode == "external-jwt-missing-token":
        expect_external_jwt_missing_token(args)
    elif mode == "external-jwt-claims":
        print_external_idp_claims()
    elif mode == "manual":
        print("Manual scenario step. Follow the scenario evidence instructions.")
    else:
        raise RuntimeError(f"Unsupported scenario test mode: {mode}")


def write_test_evidence(
    output_dir: Path,
    test_name: str,
    stdout_text: str,
    stderr_text: str,
    result: dict[str, Any],
) -> None:
    safe_name = test_name.replace("/", "_").replace("\\", "_")
    stdout_text = redact_sensitive_text(stdout_text)
    stderr_text = redact_sensitive_text(stderr_text)
    result = redact_sensitive_obj(result)
    (output_dir / f"{safe_name}.stdout.txt").write_text(stdout_text, encoding="utf-8")
    (output_dir / f"{safe_name}.stderr.txt").write_text(stderr_text, encoding="utf-8")
    (output_dir / f"{safe_name}.result.json").write_text(
        json.dumps(result, indent=2),
        encoding="utf-8",
    )


def run_scenario(args: argparse.Namespace) -> None:
    scenario_file = Path(args.scenario)
    scenario = load_scenario(scenario_file)
    scenario_name = scenario.get("name") or scenario_file.parent.name

    if not scenario.get("implemented", True):
        print(
            f"Scenario {scenario_name} is documented but not implemented in this codebase yet.",
            file=sys.stderr,
        )
        print(json.dumps({"scenario": scenario_name, "status": "not_implemented"}, indent=2))
        return

    previous_env = apply_env(scenario.get("environment", {}))
    output_dir = evidence_path(Path(args.evidence_dir), scenario_name)
    summary: dict[str, Any] = {
        "scenario": scenario_name,
        "scenario_file": str(scenario_file),
        "evidence_dir": str(output_dir),
        "tests": [],
    }

    try:
        for test in scenario.get("tests", []):
            test_name = test["name"]
            mode = test["mode"]
            test_args = scenario_args(test)
            stdout_buffer = StringIO()
            stderr_buffer = StringIO()
            result = {
                "name": test_name,
                "mode": mode,
                "status": "passed",
            }

            try:
                with redirect_stdout(stdout_buffer), redirect_stderr(stderr_buffer):
                    run_named_test(mode, test_args)
            except Exception as exc:
                result.update(
                    {
                        "status": "failed",
                        "error_type": type(exc).__name__,
                        "error": str(exc),
                    }
                )

            stdout_text = stdout_buffer.getvalue()
            stderr_text = stderr_buffer.getvalue()
            write_test_evidence(output_dir, test_name, stdout_text, stderr_text, result)
            summary["tests"].append(result)

            if result["status"] != "passed" and not args.continue_on_failure:
                break
    finally:
        restore_env(previous_env)

    summary["status"] = (
        "passed"
        if all(test["status"] == "passed" for test in summary["tests"])
        else "failed"
    )
    (output_dir / "summary.json").write_text(
        json.dumps(summary, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(summary, indent=2))

    if summary["status"] != "passed":
        sys.exit(1)


def main() -> None:
    parser = argparse.ArgumentParser(description="Prompt 4 identity and trust boundary tests.")
    parser.add_argument(
        "mode",
        choices=[
            "runtime-authorized",
            "runtime-unauthorized",
            "gateway-authorized",
            "gateway-unauthorized",
            "lambda-workload-authorized",
            "lambda-workload-unauthorized",
            "oauth-client-authorized",
            "oauth-client-missing-token",
            "external-jwt-authorized",
            "external-jwt-missing-token",
            "external-jwt-claims",
            "manual",
            "run-scenario",
        ],
    )
    parser.add_argument("scenario", nargs="?", help="Scenario JSON file for run-scenario mode.")
    parser.add_argument("--profile", help="AWS profile to use for runtime tests.")
    parser.add_argument("--role-arn", help="Role ARN to assume for runtime tests.")
    parser.add_argument("--access-key-id", help="IAM user access key ID for runtime tests.")
    parser.add_argument("--secret-access-key", help="IAM user secret access key for runtime tests.")
    parser.add_argument("--session-token", help="Optional session token for runtime tests.")
    parser.add_argument("--runtime-arn", help="Runtime ARN override.")
    parser.add_argument("--function-name", help="Lambda function name for workload tests.")
    parser.add_argument(
        "--evidence-dir",
        default=str(DEFAULT_EVIDENCE_DIR),
        help="Directory where run-scenario writes evidence.",
    )
    parser.add_argument(
        "--continue-on-failure",
        action="store_true",
        help="Run remaining scenario tests after a failure.",
    )
    args = parser.parse_args()

    if args.mode == "run-scenario":
        if not args.scenario:
            raise RuntimeError("run-scenario requires a scenario JSON file")
        run_scenario(args)
    elif args.mode == "runtime-authorized":
        expect_runtime_authorized(args)
    elif args.mode == "runtime-unauthorized":
        expect_runtime_unauthorized(args)
    elif args.mode == "gateway-authorized":
        expect_gateway_authorized()
    elif args.mode == "gateway-unauthorized":
        expect_gateway_unauthorized()
    elif args.mode == "lambda-workload-authorized":
        expect_lambda_workload_authorized(args)
    elif args.mode == "lambda-workload-unauthorized":
        expect_lambda_workload_unauthorized(args)
    elif args.mode == "oauth-client-authorized":
        expect_oauth_client_authorized(args)
    elif args.mode == "oauth-client-missing-token":
        expect_oauth_client_missing_token(args)
    elif args.mode == "external-jwt-authorized":
        expect_external_jwt_authorized(args)
    elif args.mode == "external-jwt-missing-token":
        expect_external_jwt_missing_token(args)
    elif args.mode == "external-jwt-claims":
        print_external_idp_claims()
    elif args.mode == "manual":
        print("Manual scenario step. Follow the scenario evidence instructions.")


if __name__ == "__main__":
    main()
