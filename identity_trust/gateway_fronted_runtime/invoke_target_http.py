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


IDENTITY_DIR = Path(__file__).resolve().parent.parent


def load_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip()
    return values


def env_or_file(name: str, file_values: dict[str, str], default: str | None = None) -> str | None:
    return os.getenv(name) or file_values.get(name) or default


def boto3_session(profile: str | None, role_arn: str | None, region: str) -> boto3.Session:
    base_session = boto3.Session(profile_name=profile) if profile else boto3.Session()
    if not role_arn:
        return base_session

    sts = base_session.client("sts", region_name=region)
    credentials = sts.assume_role(
        RoleArn=role_arn,
        RoleSessionName="prompt4-gateway-target-http-test",
    )["Credentials"]
    return boto3.Session(
        aws_access_key_id=credentials["AccessKeyId"],
        aws_secret_access_key=credentials["SecretAccessKey"],
        aws_session_token=credentials["SessionToken"],
        region_name=region,
    )


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
    aws_request = AWSRequest(method=method, url=url, data=body, headers=headers)
    SigV4Auth(frozen, "bedrock-agentcore", region).add_auth(aws_request)
    return dict(aws_request.headers.items())


def main() -> None:
    parser = argparse.ArgumentParser(description="Invoke a Gateway Runtime target through its raw /invocations URL.")
    parser.add_argument("--profile")
    parser.add_argument("--role-arn")
    parser.add_argument("--region", default=os.getenv("AWS_REGION", "eu-west-2"))
    parser.add_argument("--env-file", default=str(IDENTITY_DIR / "gateway_fronted_runtime_oauth_jwt.env"))
    parser.add_argument("--gateway-url")
    parser.add_argument("--target-name")
    parser.add_argument("--session-id", default=f"gateway-target-http-{uuid.uuid4()}")
    args = parser.parse_args()

    values = load_env_file(Path(args.env_file))
    gateway_url = args.gateway_url or env_or_file(
        "PROMPT4_RUNTIME_FRONTDOOR_OAUTH_GATEWAY_URL",
        values,
    ) or env_or_file(
        "PROMPT4_RUNTIME_FRONTDOOR_IAM_GATEWAY_URL",
        values,
    ) or env_or_file(
        "PROMPT4_RUNTIME_FRONTDOOR_CALLER_IAM_GATEWAY_URL",
        values,
    )
    target_name = args.target_name or env_or_file(
        "PROMPT4_RUNTIME_FRONTDOOR_OAUTH_TARGET_NAME",
        values,
    ) or env_or_file(
        "PROMPT4_RUNTIME_FRONTDOOR_IAM_TARGET_NAME",
        values,
    ) or env_or_file(
        "PROMPT4_RUNTIME_FRONTDOOR_CALLER_IAM_TARGET_NAME",
        values,
    )
    if not gateway_url or not target_name:
        raise RuntimeError("Gateway URL and target name are required")

    url = f"{gateway_url.rstrip('/')}/{target_name}/invocations"
    payload: dict[str, Any] = {
        "prompt": (
            "Call calculate_order_total with exactly these arguments: "
            "sku is SKU-BOOK-001, quantity is 3, and unit_price is 12.50."
        ),
        "tool_choice": "calculate_order_total",
    }
    body = json.dumps(payload).encode("utf-8")
    headers = {
        "content-type": "application/json",
        "accept": "application/json",
        "x-amzn-bedrock-agentcore-runtime-session-id": args.session_id,
    }

    session = boto3_session(args.profile, args.role_arn, args.region)
    signed = signed_headers(session, args.region, "POST", url, body, headers)
    response = httpx.post(url, content=body, headers=signed, timeout=180)
    print(
        json.dumps(
            {
                "status_code": response.status_code,
                "headers": {
                    "content-type": response.headers.get("content-type"),
                    "x-amzn-requestid": response.headers.get("x-amzn-requestid"),
                },
                "url": url,
                "text": response.text,
            },
            indent=2,
        )
    )
    response.raise_for_status()


if __name__ == "__main__":
    main()

