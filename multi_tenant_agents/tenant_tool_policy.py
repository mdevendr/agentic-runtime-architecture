from __future__ import annotations

from dataclasses import dataclass

from dynamodb_tenant_store import get_tenant_configuration


@dataclass(frozen=True)
class TenantRuntimeProfile:
    tenant_id: str
    allowed_tools: tuple[str, ...]
    memory_namespace: str
    rate_limit_tier: str
    outbound_credential_profile: str
    model_profile: str


@dataclass(frozen=True)
class ToolPolicyDecision:
    tenant_profile: TenantRuntimeProfile | None
    requested_tool: str
    allowed: bool
    reason: str


def profile_for_tenant(tenant_id: str) -> TenantRuntimeProfile | None:
    config = get_tenant_configuration(tenant_id)

    if config is None:
        return None

    return TenantRuntimeProfile(
        tenant_id=config["tenant_id"],
        allowed_tools=tuple(config["allowed_tools"]),
        memory_namespace=config["memory_namespace"],
        rate_limit_tier=config["rate_limit_tier"],
        outbound_credential_profile=config["outbound_credential_profile"],
        model_profile=config["model_profile"],
    )


def authorize_tool(tenant_id: str, requested_tool: str) -> ToolPolicyDecision:
    tenant_profile = profile_for_tenant(tenant_id)

    if tenant_profile is None:
        return ToolPolicyDecision(
            tenant_profile=None,
            requested_tool=requested_tool,
            allowed=False,
            reason="unknown_tenant",
        )

    if requested_tool not in tenant_profile.allowed_tools:
        return ToolPolicyDecision(
            tenant_profile=tenant_profile,
            requested_tool=requested_tool,
            allowed=False,
            reason="tool_not_in_tenant_catalog",
        )

    return ToolPolicyDecision(
        tenant_profile=tenant_profile,
        requested_tool=requested_tool,
        allowed=True,
        reason="tool_allowed_for_verified_tenant",
    )
