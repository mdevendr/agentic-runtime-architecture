from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from dynamodb_tenant_store import get_tenant_order, record_refund
from tenant_tool_policy import authorize_tool


@dataclass(frozen=True)
class VerifiedTenantContext:
    tenant_id: str
    subject: str
    source: str
    correlation_id: str


def execute_tool(tenant_id: str, tool_name: str, tool_input: dict[str, Any]) -> dict[str, Any]:
    if tool_name == "check_order":
        order = get_tenant_order(tenant_id, tool_input["order_id"])
        if order is None:
            raise ValueError("Order does not exist inside the tenant data partition.")
        return order

    if tool_name == "create_refund":
        return record_refund(tenant_id, tool_input["order_id"])

    raise ValueError(f"Unsupported tool: {tool_name}")


def handle_tool_request(
    verified_context: VerifiedTenantContext,
    requested_tool: str,
    tool_input: dict[str, Any],
) -> dict[str, Any]:
    decision = authorize_tool(verified_context.tenant_id, requested_tool)
    tenant_profile = decision.tenant_profile

    response: dict[str, Any] = {
        "tenant_id": verified_context.tenant_id,
        "subject": verified_context.subject,
        "caller_context_source": verified_context.source,
        "correlation_id": verified_context.correlation_id,
        "requested_tool": requested_tool,
        "authorization_decision": decision.reason,
        "allowed": decision.allowed,
    }

    if tenant_profile is not None:
        response.update(
            {
                "allowed_tools": list(tenant_profile.allowed_tools),
                "memory_namespace": tenant_profile.memory_namespace,
                "rate_limit_tier": tenant_profile.rate_limit_tier,
                "outbound_credential_profile": tenant_profile.outbound_credential_profile,
                "model_profile": tenant_profile.model_profile,
            }
        )
    else:
        response["allowed_tools"] = []

    if not decision.allowed:
        response["tool_executed"] = False
        response["result"] = {
            "error": "Tenant is not authorized to invoke the requested tool."
        }
        return response

    response["tool_executed"] = True
    response["result"] = execute_tool(
        tenant_id=verified_context.tenant_id,
        tool_name=requested_tool,
        tool_input=tool_input,
    )
    return response


def tenant_context_from_verified_claims(claims: dict[str, str]) -> VerifiedTenantContext:
    required = ("tenant_id", "sub", "source", "correlation_id")
    missing = [key for key in required if not claims.get(key)]

    if missing:
        raise ValueError(f"Missing verified tenant context fields: {', '.join(missing)}")

    return VerifiedTenantContext(
        tenant_id=claims["tenant_id"],
        subject=claims["sub"],
        source=claims["source"],
        correlation_id=claims["correlation_id"],
    )


def describe_context(context: VerifiedTenantContext) -> dict[str, Any]:
    return asdict(context)
