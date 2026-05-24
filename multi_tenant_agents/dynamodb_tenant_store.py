from __future__ import annotations

from copy import deepcopy
from typing import Any


TENANT_CONFIGURATION_TABLE: dict[str, dict[str, Any]] = {
    "tenant-a": {
        "tenant_id": "tenant-a",
        "allowed_tools": ("check_order", "create_refund"),
        "memory_namespace": "memory/tenant-a",
        "rate_limit_tier": "premium",
        "outbound_credential_profile": "tenant-a-refund-worker",
        "model_profile": "standard-reasoning",
    },
    "tenant-b": {
        "tenant_id": "tenant-b",
        "allowed_tools": ("check_order",),
        "memory_namespace": "memory/tenant-b",
        "rate_limit_tier": "standard",
        "outbound_credential_profile": "tenant-b-readonly-worker",
        "model_profile": "standard-reasoning",
    },
}


TENANT_USER_DATA_TABLES: dict[str, dict[str, dict[str, Any]]] = {
    "tenant-a": {
        "order-1001": {
            "order_id": "order-1001",
            "status": "DELIVERED",
            "refund_eligible": True,
            "tenant_id": "tenant-a",
        }
    },
    "tenant-b": {
        "order-2001": {
            "order_id": "order-2001",
            "status": "DELIVERED",
            "refund_eligible": True,
            "tenant_id": "tenant-b",
        }
    },
}


def get_tenant_configuration(tenant_id: str) -> dict[str, Any] | None:
    config = TENANT_CONFIGURATION_TABLE.get(tenant_id)
    return deepcopy(config) if config else None


def get_tenant_order(tenant_id: str, order_id: str) -> dict[str, Any] | None:
    order = TENANT_USER_DATA_TABLES.get(tenant_id, {}).get(order_id)
    return deepcopy(order) if order else None


def record_refund(tenant_id: str, order_id: str) -> dict[str, Any]:
    order = get_tenant_order(tenant_id, order_id)
    if order is None:
        raise ValueError("Order does not exist inside the tenant data partition.")

    if not order["refund_eligible"]:
        raise ValueError("Order is not refund eligible.")

    return {
        "order_id": order_id,
        "refund_id": f"refund-{tenant_id}-{order_id}",
        "status": "CREATED",
        "tenant_id": tenant_id,
    }

