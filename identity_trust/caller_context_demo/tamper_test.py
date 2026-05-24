from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from identity_trust.caller_context_assertion import sign_caller_context
from identity_trust.caller_context_demo.runtime_app import runtime_handler


def main() -> None:
    token = sign_caller_context(
        claims={
            "sub": "user-123",
            "tenant": "tenant-a",
            "session_id": "session-abc",
            "correlation_id": "corr-xyz",
        },
        secret="demo-signing-secret",
        audience="agentcore-runtime:caller-context-demo",
    )

    try:
        runtime_handler(
            {
                "runtime_facing_identity": "GatewayServiceRole",
                "caller_context_assertion": token,
                "runtime_audience": "wrong-runtime-audience",
                "verification_secret": "demo-signing-secret",
            }
        )
    except ValueError as exc:
        print(f"caller context rejected: {exc}")
        return

    raise RuntimeError("caller context tamper test should have failed")


if __name__ == "__main__":
    main()

