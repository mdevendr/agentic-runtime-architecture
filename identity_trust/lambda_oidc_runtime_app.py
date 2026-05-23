import json
import os
import uuid
from html import escape
from typing import Any
from urllib.error import HTTPError
from urllib.parse import parse_qs, quote, urlencode
from urllib.request import Request, urlopen


def json_response(status_code: int, payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "statusCode": status_code,
        "headers": {"content-type": "application/json"},
        "body": json.dumps(payload),
    }


def html_response(status_code: int, body: str) -> dict[str, Any]:
    return {
        "statusCode": status_code,
        "headers": {"content-type": "text/html; charset=utf-8"},
        "body": body,
    }


def redirect_response(location: str) -> dict[str, Any]:
    return {
        "statusCode": 302,
        "headers": {"location": location},
        "body": "",
    }


def require_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"{name} is required")
    return value


def post_form(url: str, form: dict[str, str]) -> dict[str, Any]:
    data = urlencode(form).encode("utf-8")
    request = Request(
        url,
        data=data,
        headers={"content-type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    with urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def post_json(url: str, token: str, payload: dict[str, Any]) -> tuple[int, str]:
    data = json.dumps(payload).encode("utf-8")
    request = Request(
        url,
        data=data,
        headers={
            "authorization": f"Bearer {token}",
            "content-type": "application/json",
            "accept": "application/json",
            "x-amzn-bedrock-agentcore-runtime-session-id": f"api-gateway-oidc-user-{uuid.uuid4()}",
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=120) as response:
            return response.status, response.read().decode("utf-8")
    except HTTPError as exc:
        return exc.code, exc.read().decode("utf-8")


def runtime_url(runtime_arn: str, region: str) -> str:
    return (
        f"https://bedrock-agentcore.{region}.amazonaws.com"
        f"/runtimes/{quote(runtime_arn, safe='')}/invocations"
    )


def authorize_url() -> str:
    hosted_ui_base = require_env("PROMPT4_COGNITO_HOSTED_UI_BASE_URL")
    params = {
        "response_type": "code",
        "client_id": require_env("PROMPT4_COGNITO_USER_CLIENT_ID"),
        "redirect_uri": require_env("PROMPT4_OIDC_CALLBACK_URL"),
        "scope": os.getenv("PROMPT4_COGNITO_USER_SCOPES", "openid email profile agentcore-runtime/invoke"),
    }
    provider = os.getenv("PROMPT4_COGNITO_IDENTITY_PROVIDER")
    if provider:
        params["identity_provider"] = provider
    return f"{hosted_ui_base}/oauth2/authorize?{urlencode(params)}"


def exchange_code(code: str) -> dict[str, Any]:
    return post_form(
        require_env("PROMPT4_COGNITO_TOKEN_URL"),
        {
            "grant_type": "authorization_code",
            "client_id": require_env("PROMPT4_COGNITO_USER_CLIENT_ID"),
            "code": code,
            "redirect_uri": require_env("PROMPT4_OIDC_CALLBACK_URL"),
        },
    )


def invoke_runtime(access_token: str) -> tuple[int, str]:
    payload = {
        "prompt": (
            "Call calculate_order_total with exactly these arguments: "
            "sku is SKU-BOOK-001, quantity is 3, and unit_price is 12.50."
        ),
        "tool_choice": "calculate_order_total",
    }
    return post_json(
        runtime_url(
            require_env("AGENT_RUNTIME_ARN_OIDC_JWT"),
            os.getenv("AWS_REGION", "eu-west-2"),
        ),
        access_token,
        payload,
    )


def callback(query: dict[str, list[str]]) -> dict[str, Any]:
    if "error" in query:
        return json_response(
            400,
            {
                "ok": False,
                "error": query.get("error", [""])[0],
                "error_description": query.get("error_description", [""])[0],
            },
        )
    code = query.get("code", [""])[0]
    if not code:
        return json_response(400, {"ok": False, "error": "Missing authorization code"})

    token_payload = exchange_code(code)
    access_token = token_payload["access_token"]
    status_code, runtime_body = invoke_runtime(access_token)
    body = {
        "ok": 200 <= status_code < 300,
        "runtime_status_code": status_code,
        "runtime_response": runtime_body,
    }
    return html_response(
        200 if body["ok"] else 502,
        "<html><body><h1>Prompt 4 OIDC Runtime Result</h1>"
        f"<pre>{escape(json.dumps(body, indent=2))}</pre></body></html>",
    )


def handler(event: dict[str, Any], _context: Any) -> dict[str, Any]:
    path = event.get("rawPath") or event.get("path") or "/"
    if path.endswith("/start") or path == "/":
        return redirect_response(authorize_url())
    if path.endswith("/callback"):
        return callback(parse_qs(event.get("rawQueryString", "")))
    return json_response(404, {"ok": False, "error": "Not found"})
