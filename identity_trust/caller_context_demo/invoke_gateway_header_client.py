from __future__ import annotations

import argparse
import json
import os
import uuid
from pathlib import Path
from typing import Any

import boto3
import httpx
from botocore.awsrequest import AWSRequest
from botocore.auth import SigV4Auth


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


def boto3_session(profile: str | None, region: str) -> boto3.Session:
    return boto3.Session(profile_name=profile, region_name=region) if profile else boto3.Session(region_name=region)


def signed_headers(
    session: boto3.Session,
    region: str,
    method: str,
    url: str,
    body: bytes,
    headers: dict[str, str],
) -> dict[str, str]:
    credentials = session.get_credentials()
    if credentials is None:
        raise RuntimeError("AWS credentials are required")
    frozen = credentials.get_frozen_credentials()
    request = AWSRequest(method=method, url=url, data=body, headers=headers)
    SigV4Auth(frozen, "bedrock-agentcore", region).add_auth(request)
    return dict(request.headers.items())


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Invoke caller-context Runtime through Gateway interceptor signed header path."
    )
    parser.add_argument("--profile", help="AWS profile used by the client.")
    parser.add_argument("--region", default=os.getenv("AWS_REGION", "eu-west-2"))
    parser.add_argument("--runtime-env-file", default=str(BASE_DIR / "caller_context_runtime.env"))
    parser.add_argument("--gateway-env-file", default=str(BASE_DIR / "caller_context_gateway_header.env"))
    parser.add_argument("--gateway-url", default=os.getenv("CALLER_CONTEXT_HEADER_GATEWAY_URL"))
    parser.add_argument("--target-name", default=os.getenv("CALLER_CONTEXT_HEADER_TARGET_NAME"))
    parser.add_argument("--runtime-arn", default=os.getenv("CALLER_CONTEXT_RUNTIME_ARN"))
    parser.add_argument("--signing-secret", default=os.getenv("CALLER_CONTEXT_SIGNING_SECRET", "demo-signing-secret"))
    parser.add_argument("--subject", default="user-123")
    parser.add_argument("--tenant", default="tenant-a")
    parser.add_argument("--session-id", default=f"caller-context-header-{uuid.uuid4()}")
    args = parser.parse_args()

    runtime_env = load_env_file(Path(args.runtime_env_file))
    gateway_env = load_env_file(Path(args.gateway_env_file))
    gateway_url = args.gateway_url or gateway_env.get("CALLER_CONTEXT_HEADER_GATEWAY_URL")
    target_name = args.target_name or gateway_env.get("CALLER_CONTEXT_HEADER_TARGET_NAME")
    runtime_arn = args.runtime_arn or runtime_env.get("CALLER_CONTEXT_RUNTIME_ARN")
    if not gateway_url:
        raise RuntimeError("Gateway URL is required. Run setup_gateway_with_interceptor.py first.")
    if not target_name:
        raise RuntimeError("Gateway target name is required. Run setup_gateway_with_interceptor.py first.")
    if not runtime_arn:
        raise RuntimeError("Runtime ARN is required. Run deploy_runtime.py first.")

    audience = f"agentcore-runtime:{runtime_arn}"
    payload = {
        "runtime_facing_identity": "GatewayServiceRole",
        "runtime_audience": audience,
        "verification_secret": args.signing_secret,
    }

    url = f"{gateway_url.rstrip('/')}/{target_name}/invocations"
    body = json.dumps(payload).encode("utf-8")
    headers = {
        "content-type": "application/json",
        "accept": "application/json",
        "x-amzn-bedrock-agentcore-runtime-session-id": args.session_id,
        "x-demo-sub": args.subject,
        "x-demo-tenant": args.tenant,
        "x-demo-session-id": args.session_id,
        "x-correlation-id": f"corr-{uuid.uuid4()}",
    }

    session = boto3_session(args.profile, args.region)
    signed = signed_headers(session, args.region, "POST", url, body, headers)
    response = httpx.post(url, content=body, headers=signed, timeout=180)

    diagnostic = {
        "status_code": response.status_code,
        "headers": {
            "content-type": response.headers.get("content-type"),
            "x-amzn-requestid": response.headers.get("x-amzn-requestid"),
            "x-amzn-errortype": response.headers.get("x-amzn-errortype"),
            "x-amzn-errormessage": response.headers.get("x-amzn-errormessage"),
        },
        "url": url,
        "text": response.text,
    }
    print(json.dumps(diagnostic, indent=2))
    response.raise_for_status()
    print(
        json.dumps(
            {
                "gateway_url": gateway_url,
                "target_name": target_name,
                "url": url,
                "runtime_arn": runtime_arn,
                "session_id": args.session_id,
                "caller_context_source": "gateway_request_interceptor_header",
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
