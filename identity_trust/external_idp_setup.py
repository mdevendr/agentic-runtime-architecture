import argparse
import json
import os


def env_list(name: str) -> list[str]:
    return [item.strip() for item in os.getenv(name, "").split(",") if item.strip()]


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Planned setup for external_idp_direct_jwt. "
            "This validates required external IdP configuration for a direct JWT Runtime."
        )
    )
    parser.add_argument("--discovery-url", default=os.getenv("EXTERNAL_IDP_DISCOVERY_URL"))
    parser.add_argument("--allowed-clients", nargs="*", default=env_list("EXTERNAL_IDP_ALLOWED_CLIENTS"))
    parser.add_argument("--allowed-audiences", nargs="*", default=env_list("EXTERNAL_IDP_ALLOWED_AUDIENCES"))
    parser.add_argument("--allowed-scopes", nargs="*", default=env_list("EXTERNAL_IDP_ALLOWED_SCOPES"))
    parser.add_argument("--token-url", default=os.getenv("EXTERNAL_IDP_TOKEN_URL"))
    parser.add_argument("--client-id", default=os.getenv("EXTERNAL_IDP_CLIENT_ID"))
    parser.add_argument("--client-secret", default=os.getenv("EXTERNAL_IDP_CLIENT_SECRET"))
    args = parser.parse_args()

    missing = [
        name
        for name, value in {
            "EXTERNAL_IDP_DISCOVERY_URL": args.discovery_url,
            "EXTERNAL_IDP_ALLOWED_CLIENTS": args.allowed_clients,
            "EXTERNAL_IDP_ALLOWED_AUDIENCES": args.allowed_audiences,
            "EXTERNAL_IDP_ALLOWED_SCOPES": args.allowed_scopes,
            "EXTERNAL_IDP_TOKEN_URL": args.token_url,
            "EXTERNAL_IDP_CLIENT_ID": args.client_id,
            "EXTERNAL_IDP_CLIENT_SECRET": args.client_secret,
        }.items()
        if not value
    ]

    result = {
        "scenario": "external_idp_direct_jwt",
        "implemented": True,
        "ready_for_runtime_deploy": not missing,
        "missing": missing,
        "token_request": {
            "token_url": args.token_url,
            "client_id": args.client_id,
            "scope": os.getenv(
                "EXTERNAL_IDP_TOKEN_SCOPE",
                f"{args.allowed_audiences[0]}/.default" if args.allowed_audiences else None,
            ),
        },
        "runtime_authorizer_env": {
            "AGENTCORE_EXTERNAL_JWT_DISCOVERY_URL": args.discovery_url,
            "AGENTCORE_EXTERNAL_JWT_ALLOWED_CLIENTS": ",".join(args.allowed_clients),
            "AGENTCORE_EXTERNAL_JWT_ALLOWED_AUDIENCES": ",".join(args.allowed_audiences),
            "AGENTCORE_EXTERNAL_JWT_ALLOWED_SCOPES": ",".join(args.allowed_scopes),
        },
    }
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
