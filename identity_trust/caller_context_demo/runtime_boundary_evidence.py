from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from identity_trust.caller_context_assertion import sign_caller_context
from identity_trust.caller_context_demo.runtime_app import runtime_handler


RUNTIME_AUDIENCE = "agentcore-runtime:caller-context-demo"
SIGNING_SECRET = "demo-signing-secret"


def make_token(audience: str = RUNTIME_AUDIENCE, secret: str = SIGNING_SECRET) -> str:
    return sign_caller_context(
        claims={
            "sub": "user-123",
            "tenant": "tenant-a",
            "session_id": "session-abc",
            "correlation_id": "corr-xyz",
            "authorization_context": {
                "source": "trusted-caller-or-gateway",
                "mode": "identity-substitution",
            },
        },
        secret=secret,
        audience=audience,
        ttl_seconds=300,
    )


def runtime_event(token: str, audience: str = RUNTIME_AUDIENCE) -> dict[str, Any]:
    return {
        "runtime_facing_identity": "GatewayServiceRole",
        "caller_context_assertion": token,
        "runtime_audience": audience,
        "verification_secret": SIGNING_SECRET,
    }


def capture_runtime_decision(name: str, event: dict[str, Any]) -> dict[str, Any]:
    try:
        response = runtime_handler(event)
        return {
            "case": name,
            "boundary": "agentcore-runtime-application",
            "decision": "accepted",
            "response": response,
        }
    except Exception as exc:
        return {
            "case": name,
            "boundary": "agentcore-runtime-application",
            "decision": "rejected",
            "error": type(exc).__name__,
            "message": str(exc),
        }


def tamper_signature(token: str) -> str:
    header, payload, _signature = token.split(".")
    return f"{header}.{payload}.tampered-signature"


def main() -> None:
    good_token = make_token()
    wrong_audience_token = make_token(audience="agentcore-runtime:other-runtime")
    tampered_token = tamper_signature(good_token)

    evidence = {
        "runtime_boundary": "AgentCore Runtime application verifies caller-context assertion",
        "good": capture_runtime_decision("valid assertion", runtime_event(good_token)),
        "bad_wrong_audience": capture_runtime_decision(
            "wrong audience",
            runtime_event(wrong_audience_token),
        ),
        "bad_tampered_signature": capture_runtime_decision(
            "tampered signature",
            runtime_event(tampered_token),
        ),
    }
    print(json.dumps(evidence, indent=2))


if __name__ == "__main__":
    main()
