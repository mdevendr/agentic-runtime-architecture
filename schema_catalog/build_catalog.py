from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any


def canonical_hash(value: dict[str, Any]) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def build_catalog(schema_path: Path, owner: str, credential_mode: str) -> dict[str, Any]:
    schemas = json.loads(schema_path.read_text(encoding="utf-8"))
    tools = []

    for schema in schemas:
        input_schema = schema["inputSchema"]
        tools.append(
            {
                "name": schema["name"],
                "schemaVersion": schema.get("schemaVersion", "1.0.0"),
                "inputContractHash": canonical_hash(input_schema),
                "targetBinding": schema.get("targetBinding", f"{schema['name']}Target"),
                "credentialMode": schema.get("credentialMode", credential_mode),
                "owner": schema.get("owner", owner),
                "deprecated": schema.get("deprecated", False),
                "inputSchema": input_schema,
            }
        )

    return {
        "catalogVersion": "1.0.0",
        "sourceSchema": str(schema_path),
        "catalogHash": canonical_hash({"tools": tools}),
        "tools": tools,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Build a static tool schema catalog.")
    parser.add_argument(
        "--schema-path",
        default="agentcore_runtime_gateway_tools_boundary/tool_schema.json",
    )
    parser.add_argument("--owner", default="orders-platform")
    parser.add_argument("--credential-mode", default="GATEWAY_IAM_ROLE")
    parser.add_argument("--output", default="schema_catalog/catalog.example.json")
    args = parser.parse_args()

    catalog = build_catalog(Path(args.schema_path), args.owner, args.credential_mode)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(catalog, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {output} with {len(catalog['tools'])} tools")


if __name__ == "__main__":
    main()

