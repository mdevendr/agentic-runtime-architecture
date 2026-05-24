from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent
GATEWAY_SETUP = BASE_DIR.parent / "gateway_fronted_runtime" / "iam_sigv4_setup.py"


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


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Create Gateway-fronted Runtime substitution target for caller-context demo."
    )
    parser.add_argument("--profile", help="AWS profile to use.")
    parser.add_argument("--region", default=os.getenv("AWS_REGION", "eu-west-2"))
    parser.add_argument("--runtime-env-file", default=str(BASE_DIR / "caller_context_runtime.env"))
    parser.add_argument("--runtime-arn", default=os.getenv("CALLER_CONTEXT_RUNTIME_ARN"))
    parser.add_argument("--gateway-name", default="caller-context-frontdoor")
    parser.add_argument("--target-name", default="callerContextRuntimeTarget")
    parser.add_argument("--role-name", default="CallerContextGatewayRole")
    parser.add_argument("--env-file", default=str(BASE_DIR / "caller_context_gateway.env"))
    args = parser.parse_args()

    runtime_env = load_env_file(Path(args.runtime_env_file))
    runtime_arn = args.runtime_arn or runtime_env.get("CALLER_CONTEXT_RUNTIME_ARN")
    if not runtime_arn:
        raise RuntimeError("CALLER_CONTEXT_RUNTIME_ARN is required. Deploy the Runtime first.")

    command = [
        sys.executable,
        str(GATEWAY_SETUP),
        "--region",
        args.region,
        "--runtime-arn",
        runtime_arn,
        "--gateway-name",
        args.gateway_name,
        "--target-name",
        args.target_name,
        "--role-name",
        args.role_name,
        "--env-file",
        args.env_file,
    ]
    if args.profile:
        command.extend(["--profile", args.profile])

    subprocess.run(command, check=True)


if __name__ == "__main__":
    main()

