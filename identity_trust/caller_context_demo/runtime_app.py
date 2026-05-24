from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from identity_trust.caller_context_assertion import verify_caller_context


def who_am_i_tool(
    runtime_facing_identity: str,
    caller_context_token: str,
    verification_secret: str,
    runtime_audience: str,
) -> dict[str, Any]:
    """
    Runtime-side evidence for identity substitution hardening.

    AgentCore Runtime authorization has already accepted the substituted
    Runtime-facing identity. This tool verifies the caller-context assertion
    that travels alongside that substituted identity.
    """

    caller = verify_caller_context(
        token=caller_context_token,
        secret=verification_secret,
        audience=runtime_audience,
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


def runtime_handler(event: dict[str, Any]) -> dict[str, Any]:
    runtime_facing_identity = event["runtime_facing_identity"]
    caller_context_token = event["caller_context_assertion"]
    verification_secret = event["verification_secret"]
    runtime_audience = event["runtime_audience"]

    return who_am_i_tool(
        runtime_facing_identity=runtime_facing_identity,
        caller_context_token=caller_context_token,
        verification_secret=verification_secret,
        runtime_audience=runtime_audience,
    )

