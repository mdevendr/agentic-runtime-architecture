import argparse
import json
import os
import uuid
from pathlib import Path
from typing import Any

import httpx


IDENTITY_DIR = Path(__file__).resolve().parent.parent
IDENTITY_PROVIDER_ENV = IDENTITY_DIR / "identity_provider.env"


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


def env_or_file(name: str, values: dict[str, str], default: str | None = None) -> str | None:
    return os.getenv(name) or values.get(name) or default


def get_cognito_token(values: dict[str, str]) -> str:
    token_url = env_or_file("PROMPT4_COGNITO_TOKEN_URL", values)
    client_id = env_or_file("PROMPT4_COGNITO_MACHINE_CLIENT_ID", values)
    client_secret = env_or_file("PROMPT4_COGNITO_MACHINE_CLIENT_SECRET", values)
    scope = env_or_file("PROMPT4_COGNITO_SCOPE", values, "agentcore-runtime/invoke")
    if not token_url or not client_id or not client_secret:
        raise RuntimeError("Cognito token URL, client ID, and client secret are required")

    response = httpx.post(
        token_url,
        data={"grant_type": "client_credentials", "scope": scope},
        auth=(client_id, client_secret),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=30,
    )
    response.raise_for_status()
    return response.json()["access_token"]


def main() -> None:
    parser = argparse.ArgumentParser(description="Invoke JWT passthrough Gateway Runtime target with a Cognito bearer token.")
    parser.add_argument("--env-file", default=str(IDENTITY_DIR / "gateway_fronted_runtime_jwt_passthrough.env"))
    parser.add_argument("--identity-env-file", default=str(IDENTITY_PROVIDER_ENV))
    parser.add_argument("--gateway-url")
    parser.add_argument("--target-name")
    parser.add_argument("--session-id", default=f"gateway-jwt-pass-{uuid.uuid4()}")
    args = parser.parse_args()

    gateway_values = load_env_file(Path(args.env_file))
    identity_values = load_env_file(Path(args.identity_env_file))
    gateway_url = args.gateway_url or env_or_file("PROMPT4_RUNTIME_FRONTDOOR_JWT_GATEWAY_URL", gateway_values)
    target_name = args.target_name or env_or_file("PROMPT4_RUNTIME_FRONTDOOR_JWT_TARGET_NAME", gateway_values)
    if not gateway_url or not target_name:
        raise RuntimeError("Gateway URL and target name are required")

    token = get_cognito_token(identity_values)
    url = f"{gateway_url.rstrip('/')}/{target_name}/invocations"
    payload: dict[str, Any] = {
        "prompt": (
            "Call calculate_order_total with exactly these arguments: "
            "sku is SKU-BOOK-001, quantity is 3, and unit_price is 12.50."
        ),
        "tool_choice": "calculate_order_total",
    }
    headers = {
        "content-type": "application/json",
        "accept": "application/json",
        "authorization": f"Bearer {token}",
        "x-amzn-bedrock-agentcore-runtime-session-id": args.session_id,
    }
    response = httpx.post(url, headers=headers, json=payload, timeout=180)
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

