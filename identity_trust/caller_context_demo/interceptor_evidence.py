from __future__ import annotations

import json
import os
import sys
import base64
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))

from caller_context_assertion import verify_caller_context
from gateway_caller_context_interceptor import CALLER_CONTEXT_HEADER, lambda_handler


def gateway_event(headers: dict[str, str], include_identity: bool = True) -> dict:
    body = {
        "runtime_facing_identity": "GatewayServiceRole",
        "runtime_audience": "agentcore-runtime:demo",
        "verification_secret": "demo-signing-secret",
    }
    event = {
        "interceptorInputVersion": "1.0",
        "requestContext": {
            "requestId": "req-123",
            "identity": {},
        },
        "http": {
            "gatewayRequest": {
                "path": "/callerContextHeaderRuntimeTarget/invocations",
                "httpMethod": "POST",
                "headers": headers,
                "body": base64.b64encode(json.dumps(body).encode("utf-8")).decode("ascii"),
            }
        },
    }
    if include_identity:
        event["requestContext"]["identity"] = {
            "userArn": "arn:aws:iam::111122223333:role/demo-caller",
        }
    return event


def main() -> None:
    os.environ["CALLER_CONTEXT_SIGNING_SECRET"] = "demo-signing-secret"
    os.environ["CALLER_CONTEXT_RUNTIME_AUDIENCE"] = "agentcore-runtime:demo"

    good = lambda_handler(
        gateway_event(
            {
                "x-demo-sub": "user-123",
                "x-demo-tenant": "tenant-a",
                "x-demo-session-id": "session-abc",
                "x-correlation-id": "corr-xyz",
            }
        ),
        None,
    )
    token = good["http"]["transformedGatewayRequest"]["headers"][CALLER_CONTEXT_HEADER]
    verified = verify_caller_context(
        token=token,
        secret="demo-signing-secret",
        audience="agentcore-runtime:demo",
    )

    bad = lambda_handler(
        gateway_event({"x-demo-tenant": "tenant-a"}, include_identity=False),
        None,
    )

    print(
        json.dumps(
            {
                "good_interceptor_response": {
                    "has_transformed_gateway_request": "transformedGatewayRequest" in good["http"],
                    "signed_header": CALLER_CONTEXT_HEADER,
                    "verified_subject": verified["sub"],
                    "verified_tenant": verified["tenant"],
                    "verified_source": verified["authorization_context"]["source"],
                },
                "bad_interceptor_response": bad,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
