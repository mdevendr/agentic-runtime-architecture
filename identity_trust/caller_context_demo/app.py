from __future__ import annotations

import logging
from typing import Any

from bedrock_agentcore import BedrockAgentCoreApp, RequestContext

from caller_context_assertion import verify_caller_context


CALLER_CONTEXT_HEADER = "x-amzn-bedrock-agentcore-runtime-custom-caller-context-assertion"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)

app = BedrockAgentCoreApp()


def header_value(headers: dict[str, Any], name: str) -> str | None:
    for key, value in headers.items():
        if key.lower() == name.lower():
            return str(value)
    return None


def who_am_i_tool(
    runtime_facing_identity: str,
    caller_context_token: str,
    verification_secret: str,
    runtime_audience: str,
) -> dict[str, Any]:
    caller = verify_caller_context(
        token=caller_context_token,
        secret=verification_secret,
        audience=runtime_audience,
    )
    logging.info(
        "caller_context_verified runtime_authorized_identity=%s original_caller=%s tenant=%s session_id=%s correlation_id=%s source=%s",
        runtime_facing_identity,
        caller["sub"],
        caller["tenant"],
        caller["session_id"],
        caller["correlation_id"],
        caller.get("authorization_context", {}).get("source"),
    )

    return {
        "runtime_authorized_identity": runtime_facing_identity,
        "caller_context_verified": True,
        "original_caller": caller["sub"],
        "tenant": caller["tenant"],
        "session_id": caller["session_id"],
        "correlation_id": caller["correlation_id"],
        "authorization_context": caller.get("authorization_context", {}),
    }


@app.entrypoint
def agentcore_entrypoint(
    request: dict[str, Any],
    context: RequestContext | None = None,
) -> dict[str, Any]:
    """
    Deployed Runtime entrypoint for substitution-mode caller context evidence.

    AgentCore Runtime authorizes the Gateway-fronted substituted identity before
    this code runs. This application validates the signed caller-context
    assertion separately to recover original caller attribution.

    Evidence lane 1 keeps the assertion in the JSON payload to model a trusted
    client or external authority signing for itself. Evidence lane 2 reads the
    assertion from a Runtime custom header injected by a Gateway REQUEST
    interceptor.
    """

    runtime_facing_identity = request.get("runtime_facing_identity", "GatewayServiceRole")
    request_headers = context.request_headers if context else {}
    header_token = header_value(request_headers, CALLER_CONTEXT_HEADER)
    token = header_token or request.get("caller_context_assertion")
    secret = request.get("verification_secret")
    audience = request.get("runtime_audience")

    if not token:
        return {
            "error": (
                "caller_context_assertion payload field or "
                "X-Amzn-Bedrock-AgentCore-Runtime-Custom-Caller-Context-Assertion header is required"
            )
        }
    if not secret:
        return {"error": "verification_secret is required for demo validation"}
    if not audience:
        return {"error": "runtime_audience is required"}

    try:
        result = who_am_i_tool(
            runtime_facing_identity=runtime_facing_identity,
            caller_context_token=token,
            verification_secret=secret,
            runtime_audience=audience,
        )
        result["caller_context_source"] = "gateway_header" if header_token else "payload"
        return result
    except Exception as exc:
        logging.exception("caller context validation failed")
        return {
            "runtime_authorized_identity": runtime_facing_identity,
            "caller_context_verified": False,
            "error": type(exc).__name__,
            "message": str(exc),
        }


if __name__ == "__main__":
    app.run()
