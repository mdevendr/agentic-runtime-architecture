import argparse
import json
import os
import uuid
from pathlib import Path
from typing import Any

import boto3
from botocore.exceptions import ClientError


IDENTITY_DIR = Path(__file__).resolve().parent.parent


def read_response(response: dict[str, Any]) -> str:
    if "text/event-stream" in response.get("contentType", ""):
        chunks = []
        for line in response["response"].iter_lines(chunk_size=10):
            if line:
                text = line.decode("utf-8")
                if text.startswith("data: "):
                    text = text[6:]
                chunks.append(text)
        return "\n".join(chunks)

    chunks = []
    for chunk in response.get("response", []):
        chunks.append(chunk.decode("utf-8"))
    return "".join(chunks)


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


def boto3_session(
    profile: str | None,
    role_arn: str | None,
    region: str,
) -> boto3.Session:
    base_session = boto3.Session(profile_name=profile) if profile else boto3.Session()
    if not role_arn:
        return base_session

    sts = base_session.client("sts", region_name=region)
    credentials = sts.assume_role(
        RoleArn=role_arn,
        RoleSessionName="prompt4-gateway-frontdoor-test",
    )["Credentials"]
    return boto3.Session(
        aws_access_key_id=credentials["AccessKeyId"],
        aws_secret_access_key=credentials["SecretAccessKey"],
        aws_session_token=credentials["SessionToken"],
        region_name=region,
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Invoke AgentCore Runtime through the Gateway-fronted Runtime IAM/SigV4 target."
    )
    parser.add_argument("--profile", help="AWS profile used by the client to sign the Gateway request.")
    parser.add_argument("--role-arn", help="Optional role to assume before signing the Gateway request.")
    parser.add_argument("--region", default=os.getenv("AWS_REGION", "eu-west-2"))
    parser.add_argument(
        "--env-file",
        default=str(IDENTITY_DIR / "gateway_fronted_runtime_iam.env"),
        help="Env file written by iam_sigv4_setup.py.",
    )
    parser.add_argument(
        "--gateway-url",
        help="Gateway URL. Defaults to PROMPT4_RUNTIME_FRONTDOOR_IAM_GATEWAY_URL.",
    )
    parser.add_argument(
        "--target-name",
        default=os.getenv("PROMPT4_RUNTIME_FRONTDOOR_IAM_TARGET_NAME"),
    )
    parser.add_argument(
        "--runtime-arn",
        default=os.getenv("AGENT_RUNTIME_ARN_IAM") or os.getenv("AGENT_RUNTIME_ARN"),
        help=(
            "Runtime ARN sent to InvokeAgentRuntime. Defaults to AGENT_RUNTIME_ARN_IAM "
            "or AGENT_RUNTIME_ARN. If omitted, the script uses a placeholder because "
            "the Gateway target already binds to the Runtime."
        ),
    )
    parser.add_argument("--session-id", default=f"gateway-frontdoor-{uuid.uuid4()}")
    parser.add_argument(
        "--mode",
        choices=["success", "failure"],
        default="success",
        help="Use failure to exercise Runtime/tool validation through the Gateway.",
    )
    args = parser.parse_args()

    file_values = load_env_file(Path(args.env_file))
    gateway_url = args.gateway_url or env_or_file(
        "PROMPT4_RUNTIME_FRONTDOOR_IAM_GATEWAY_URL",
        file_values,
    ) or env_or_file(
        "PROMPT4_RUNTIME_FRONTDOOR_CALLER_IAM_GATEWAY_URL",
        file_values,
    ) or env_or_file(
        "PROMPT4_RUNTIME_FRONTDOOR_OAUTH_GATEWAY_URL",
        file_values,
    )
    target_name = args.target_name or env_or_file(
        "PROMPT4_RUNTIME_FRONTDOOR_IAM_TARGET_NAME",
        file_values,
    ) or env_or_file(
        "PROMPT4_RUNTIME_FRONTDOOR_CALLER_IAM_TARGET_NAME",
        file_values,
    ) or env_or_file(
        "PROMPT4_RUNTIME_FRONTDOOR_OAUTH_TARGET_NAME",
        file_values,
    ) or "prompt4RuntimeIamSigv4Target"
    if (
        target_name == "prompt4RuntimeIamSigv4Target"
        and "PROMPT4_RUNTIME_FRONTDOOR_CALLER_IAM_GATEWAY_URL" in file_values
    ):
        target_name = "prompt4RuntimeCallerIamTarget"
    if (
        target_name == "prompt4RuntimeIamSigv4Target"
        and "PROMPT4_RUNTIME_FRONTDOOR_OAUTH_GATEWAY_URL" in file_values
    ):
        target_name = "prompt4RuntimeOauthJwtTarget"
    if not gateway_url:
        raise RuntimeError(
            "Gateway URL is required. Run iam_sigv4_setup.py first or pass --gateway-url."
        )

    endpoint_url = f"{gateway_url.rstrip('/')}/{target_name}"
    runtime_arn = args.runtime_arn or "gateway-runtime-target"

    if args.mode == "success":
        prompt = (
            "Call calculate_order_total with exactly these arguments: "
            "sku is SKU-BOOK-001, quantity is 3, and unit_price is 12.50."
        )
    else:
        prompt = (
            "Call calculate_order_total with exactly these arguments: "
            "sku is BOOK-001, quantity is 3, and unit_price is 12.50. "
            "Do not rewrite the SKU. Let tool validation determine whether the SKU is valid."
        )

    payload = {
        "prompt": prompt,
        "tool_choice": "calculate_order_total",
    }

    session = boto3_session(args.profile, args.role_arn, args.region)
    client = session.client(
        "bedrock-agentcore",
        region_name=args.region,
        endpoint_url=endpoint_url,
    )
    try:
        response = client.invoke_agent_runtime(
            agentRuntimeArn=runtime_arn,
            runtimeSessionId=args.session_id,
            contentType="application/json",
            accept="application/json",
            payload=json.dumps(payload).encode("utf-8"),
        )
    except ClientError as exc:
        print(
            json.dumps(
                {
                    "error": str(exc),
                    "response": exc.response,
                    "gateway_url": gateway_url,
                    "target_name": target_name,
                    "endpoint_url": endpoint_url,
                    "runtime_arn_parameter": runtime_arn,
                    "runtime_session_id": args.session_id,
                },
                indent=2,
                default=str,
            )
        )
        raise

    response_text = read_response(response)
    print(response_text)
    print(
        json.dumps(
            {
                "gateway_url": gateway_url,
                "target_name": target_name,
                "endpoint_url": endpoint_url,
                "runtime_arn_parameter": runtime_arn,
                "runtime_session_id": args.session_id,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
