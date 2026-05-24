from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CognitoClaims:
    iss: str
    aud: str
    sub: str
    tenant_id: str
    scope: str
    correlation_id: str

    def as_verified_context(self) -> dict[str, str]:
        return {
            "tenant_id": self.tenant_id,
            "sub": self.sub,
            "source": "cognito-jwt-claims",
            "correlation_id": self.correlation_id,
        }


COGNITO_ISSUER = "https://cognito-idp.eu-west-2.amazonaws.com/eu-west-2_example"
COGNITO_CLIENT_ID = "agentic-runtime-demo-client"


def verified_cognito_claims_for_user(user_key: str, correlation_id: str) -> CognitoClaims:
    users = {
        "tenant-a-user": CognitoClaims(
            iss=COGNITO_ISSUER,
            aud=COGNITO_CLIENT_ID,
            sub="user-a-123",
            tenant_id="tenant-a",
            scope="agent.runtime.invoke",
            correlation_id=correlation_id,
        ),
        "tenant-b-user": CognitoClaims(
            iss=COGNITO_ISSUER,
            aud=COGNITO_CLIENT_ID,
            sub="user-b-456",
            tenant_id="tenant-b",
            scope="agent.runtime.invoke",
            correlation_id=correlation_id,
        ),
    }

    if user_key not in users:
        raise ValueError(f"Unknown Cognito demo user: {user_key}")

    return users[user_key]

