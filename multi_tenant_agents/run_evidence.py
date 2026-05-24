from __future__ import annotations

import json

from cognito_claims import verified_cognito_claims_for_user
from pooled_runtime_demo import handle_tool_request, tenant_context_from_verified_claims


SCENARIOS = [
    {
        "name": "tenant-a can create a refund",
        "user_key": "tenant-a-user",
        "correlation_id": "corr-tenant-a-refund",
        "requested_tool": "create_refund",
        "tool_input": {"order_id": "order-1001"},
    },
    {
        "name": "tenant-b can check an order",
        "user_key": "tenant-b-user",
        "correlation_id": "corr-tenant-b-check",
        "requested_tool": "check_order",
        "tool_input": {"order_id": "order-2001"},
    },
    {
        "name": "tenant-b cannot create a refund",
        "user_key": "tenant-b-user",
        "correlation_id": "corr-tenant-b-refund-denied",
        "requested_tool": "create_refund",
        "tool_input": {"order_id": "order-2001"},
    },
]


def run() -> list[dict[str, object]]:
    evidence = []

    for scenario in SCENARIOS:
        cognito_claims = verified_cognito_claims_for_user(
            user_key=scenario["user_key"],
            correlation_id=scenario["correlation_id"],
        )
        verified_context = tenant_context_from_verified_claims(
            cognito_claims.as_verified_context()
        )
        outcome = handle_tool_request(
            verified_context=verified_context,
            requested_tool=scenario["requested_tool"],
            tool_input=scenario["tool_input"],
        )
        evidence.append(
            {
                "scenario": scenario["name"],
                "cognito_subject": cognito_claims.sub,
                "cognito_tenant_claim": cognito_claims.tenant_id,
                "cognito_scope": cognito_claims.scope,
                **outcome,
            }
        )

    return evidence


if __name__ == "__main__":
    print(json.dumps(run(), indent=2))
