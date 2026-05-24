from __future__ import annotations

import json
import os
import base64
import time
from typing import Any

from caller_context_assertion import sign_caller_context


CALLER_CONTEXT_HEADER = "X-Amzn-Bedrock-AgentCore-Runtime-Custom-Caller-Context-Assertion"


def _headers(event: dict[str, Any]) -> dict[str, Any]:
    if "http" in event:
        gateway_request = event.get("http", {}).get("gatewayRequest", {})
        return gateway_request.get("headers", {}) or {}
    gateway_request = event.get("mcp", {}).get("gatewayRequest", {})
    return gateway_request.get("headers", {}) or {}


def _body(event: dict[str, Any]) -> Any:
    if "http" in event:
        gateway_request = event.get("http", {}).get("gatewayRequest", {})
        return gateway_request.get("body")
    gateway_request = event.get("mcp", {}).get("gatewayRequest", {})
    return gateway_request.get("body")


def _gateway_request(event: dict[str, Any]) -> dict[str, Any]:
    if "http" in event:
        return dict(event.get("http", {}).get("gatewayRequest", {}) or {})
    return dict(event.get("mcp", {}).get("gatewayRequest", {}) or {})


def _is_http_target(event: dict[str, Any]) -> bool:
    return "http" in event


def _header_value(headers: dict[str, Any], name: str, default: str = "") -> str:
    for key, value in headers.items():
        if key.lower() == name.lower():
            return str(value)
    return default


def _deny(status_code: int, code: str, message: str) -> dict[str, Any]:
    body = {
        "error": code,
        "message": message,
        "boundary": "gateway-request-interceptor",
    }
    encoded_body = base64.b64encode(json.dumps(body).encode("utf-8")).decode("ascii")
    return {
        "interceptorOutputVersion": "1.0",
        "http": {
            "transformedGatewayResponse": {
                "contentType": "application/json",
                "statusCode": status_code,
                "headers": {"Content-Type": "application/json"},
                "body": encoded_body,
            }
        },
    }


def _deny_mcp(status_code: int, code: str, message: str) -> dict[str, Any]:
    return {
        "interceptorOutputVersion": "1.0",
        "mcp": {
            "transformedGatewayResponse": {
                "statusCode": status_code,
                "body": {
                    "error": code,
                    "message": message,
                    "boundary": "gateway-request-interceptor",
                },
            }
        },
    }


def _request_context(event: dict[str, Any]) -> dict[str, Any]:
    return event.get("requestContext", {}) or {}


def _caller_claims(event: dict[str, Any]) -> dict[str, Any]:
    """
    Build caller context from Gateway-owned request data.

    Production gateways should derive these values from the authenticated
    authorizer context. The x-demo-* headers are included only so this evidence
    path can be exercised with an IAM-authenticated test client.
    """

    headers = _headers(event)
    request_context = _request_context(event)
    identity = request_context.get("identity", {}) or {}
    authorizer = request_context.get("authorizer", {}) or {}
    claims = authorizer.get("claims", {}) or {}

    subject = (
        claims.get("sub")
        or identity.get("userArn")
        or identity.get("userId")
        or _header_value(headers, "x-demo-sub")
    )
    tenant = (
        claims.get("tenant")
        or authorizer.get("tenant")
        or _header_value(headers, "x-demo-tenant")
    )
    if not subject:
        raise ValueError("missing authenticated subject")
    if not tenant:
        raise ValueError("missing tenant context")

    session_id = _header_value(headers, "x-demo-session-id", f"gateway-session-{int(time.time())}")
    correlation_id = (
        request_context.get("requestId")
        or _header_value(headers, "x-correlation-id", f"gateway-correlation-{int(time.time())}")
    )

    return {
        "sub": subject,
        "tenant": tenant,
        "session_id": session_id,
        "correlation_id": correlation_id,
        "authorization_context": {
            "source": "agentcore-gateway-request-interceptor",
            "mode": "gateway-iam-role-substitution",
            "gateway_request_id": request_context.get("requestId"),
        },
    }


def lambda_handler(event: dict[str, Any], _context: Any) -> dict[str, Any]:
    signing_secret = os.getenv("CALLER_CONTEXT_SIGNING_SECRET")
    runtime_audience = os.getenv("CALLER_CONTEXT_RUNTIME_AUDIENCE")
    deny = _deny if _is_http_target(event) else _deny_mcp
    if not signing_secret:
        return deny(500, "interceptor_misconfigured", "CALLER_CONTEXT_SIGNING_SECRET is not configured")
    if not runtime_audience:
        return deny(500, "interceptor_misconfigured", "CALLER_CONTEXT_RUNTIME_AUDIENCE is not configured")

    try:
        assertion = sign_caller_context(
            claims=_caller_claims(event),
            secret=signing_secret,
            audience=runtime_audience,
            ttl_seconds=int(os.getenv("CALLER_CONTEXT_TTL_SECONDS", "300")),
        )
    except ValueError as exc:
        return deny(400, "caller_context_rejected", str(exc))

    body = _body(event)
    if not _is_http_target(event) and isinstance(body, str):
        try:
            body = json.loads(body)
        except json.JSONDecodeError:
            pass

    transformed_request = _gateway_request(event)
    transformed_headers = dict(transformed_request.get("headers", {}) or {})
    transformed_headers[CALLER_CONTEXT_HEADER] = assertion
    transformed_request["headers"] = transformed_headers
    transformed_request["body"] = body

    if _is_http_target(event):
        return {
            "interceptorOutputVersion": "1.0",
            "http": {
                "transformedGatewayRequest": {
                    "headers": transformed_headers,
                    "body": body,
                }
            },
        }

    return {
        "interceptorOutputVersion": "1.0",
        "mcp": {
            "transformedGatewayRequest": transformed_request
        },
    }
