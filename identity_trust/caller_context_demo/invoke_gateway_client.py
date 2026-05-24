from __future__ import annotations

import argparse
import json
import os
import uuid
from pathlib import Path
from typing import Any

import boto3

from caller_context_assertion import sign_caller_context


BASE_DIR = Path(__file__).resolve().parent


def load_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip()
    return values


def read_response(response: dict[str, Any]) -> str:
    chunks = []
    for chunk in response.get("response", []):
        chunks.append(chunk.decode("utf-8"))
    return "".join(chunks)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Invoke caller-context Runtime through Gateway substitution target."
    )
    parser.add_argument("--profile", help="AWS profile used by the client.")
    parser.add_argument("--region", default=os.getenv("AWS_REGION", "eu-west-2"))
    parser.add_argument("--runtime-env-file", default=str(BASE_DIR / "caller_context_runtime.env"))
    parser.add_argument("--gateway-env-file", default=str(BASE_DIR / "caller_context_gateway.env"))
    parser.add_argument("--gateway-url", default=os.getenv("CALLER_CONTEXT_GATEWAY_URL"))
    parser.add_argument("--target-name", default=os.getenv("CALLER_CONTEXT_TARGET_NAME", "callerContextRuntimeTarget"))
    parser.add_argument("--runtime-arn", default=os.getenv("CALLER_CONTEXT_RUNTIME_ARN"))
    parser.add_argument("--signing-secret", default=os.getenv("CALLER_CONTEXT_SIGNING_SECRET", "demo-signing-secret"))
    parser.add_argument("--subject", default="user-123")
    parser.add_argument("--tenant", default="tenant-a")
    parser.add_argument("--session-id", default=f"caller-context-{uuid.uuid4()}")
    args = parser.parse_args()

    runtime_env = load_env_file(Path(args.runtime_env_file))
    gateway_env = load_env_file(Path(args.gateway_env_file))
    gateway_url = (
        args.gateway_url
        or gateway_env.get("PROMPT4_RUNTIME_FRONTDOOR_IAM_GATEWAY_URL")
    )
    runtime_arn = args.runtime_arn or runtime_env.get("CALLER_CONTEXT_RUNTIME_ARN")
    if not gateway_url:
        raise RuntimeError("Gateway URL is required. Run setup_gateway.py first.")
    if not runtime_arn:
        raise RuntimeError("Runtime ARN is required. Run deploy_runtime.py first.")

    audience = f"agentcore-runtime:{runtime_arn}"
    correlation_id = f"corr-{uuid.uuid4()}"
    token = sign_caller_context(
        claims={
            "sub": args.subject,
            "tenant": args.tenant,
            "session_id": args.session_id,
            "correlation_id": correlation_id,
            "authorization_context": {
                "source": "trusted-mediation-layer",
                "mode": "gateway-iam-role-substitution",
            },
        },
        secret=args.signing_secret,
        audience=audience,
        ttl_seconds=300,
    )

    payload = {
        "runtime_facing_identity": "GatewayServiceRole",
        "caller_context_assertion": token,
        "runtime_audience": audience,
        "verification_secret": args.signing_secret,
    }

    endpoint_url = f"{gateway_url.rstrip('/')}/{args.target_name}"
    session = boto3.Session(profile_name=args.profile) if args.profile else boto3.Session()
    client = session.client("bedrock-agentcore", region_name=args.region, endpoint_url=endpoint_url)
    response = client.invoke_agent_runtime(
        agentRuntimeArn=runtime_arn,
        runtimeSessionId=args.session_id,
        contentType="application/json",
        accept="application/json",
        payload=json.dumps(payload).encode("utf-8"),
    )

    print(read_response(response))
    print(
        json.dumps(
            {
                "gateway_url": gateway_url,
                "target_name": args.target_name,
                "endpoint_url": endpoint_url,
                "runtime_arn": runtime_arn,
                "session_id": args.session_id,
                "correlation_id": correlation_id,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()

