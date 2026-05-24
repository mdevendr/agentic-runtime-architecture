from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from identity_trust.caller_context_assertion import sign_caller_context
from identity_trust.caller_context_demo.runtime_app import runtime_handler


def main() -> None:
    runtime_audience = "agentcore-runtime:caller-context-demo"
    signing_secret = "demo-signing-secret"

    original_caller_claims = {
        "sub": "user-123",
        "tenant": "tenant-a",
        "session_id": "session-abc",
        "correlation_id": "corr-xyz",
        "authorization_context": {
            "source": "trusted-mediation-layer",
            "mode": "identity-substitution",
        },
    }

    caller_context_assertion = sign_caller_context(
        claims=original_caller_claims,
        secret=signing_secret,
        audience=runtime_audience,
        ttl_seconds=300,
    )

    event = {
        "runtime_facing_identity": "GatewayServiceRole",
        "caller_context_assertion": caller_context_assertion,
        "runtime_audience": runtime_audience,
        "verification_secret": signing_secret,
    }

    response = runtime_handler(event)
    print(json.dumps(response, indent=2))


if __name__ == "__main__":
    main()

