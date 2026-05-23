# external_idp_direct_jwt

This proves Runtime can trust a non-Cognito issuer directly.

Inputs needed from the external IdP:

```bash
export EXTERNAL_IDP_DISCOVERY_URL="https://<issuer>/.well-known/openid-configuration"
export EXTERNAL_IDP_ALLOWED_CLIENTS="<client-id>"
export EXTERNAL_IDP_ALLOWED_AUDIENCES="<audience-or-api-identifier>"
export EXTERNAL_IDP_ALLOWED_SCOPES="<required-scope>"
export EXTERNAL_IDP_TOKEN_URL="<token-endpoint>"
export EXTERNAL_IDP_CLIENT_ID="<client-id>"
export EXTERNAL_IDP_CLIENT_SECRET="<client-secret>"
export EXTERNAL_IDP_TOKEN_SCOPE="<audience>/.default"
```

Good provider choices:

- Okta custom authorization server
- Microsoft Entra ID app registration
- Auth0 API/application

Expected proof:

- Valid external IdP token invokes Runtime.
- Missing token fails.
- Wrong issuer/audience/scope can be tested by deploying a Runtime with mismatched allow lists.
- Expired token fails once the token lifetime has elapsed.

For Microsoft Entra client credentials, `EXTERNAL_IDP_TOKEN_SCOPE` is usually:

```bash
export EXTERNAL_IDP_TOKEN_SCOPE="api://<runtime-api-client-id>/.default"
```

The Runtime authorizer uses:

- discovery URL for Entra signing keys and issuer metadata
- allowed audience for the emitted access-token `aud` claim
- custom claim `azp` for the calling client app ID
- custom claim `roles` for the app role value `Runtime.Invoke`

For the tested Entra v2 app-registration setup, the token request uses:

```bash
export EXTERNAL_IDP_TOKEN_SCOPE="api://<runtime-api-client-id>/.default"
```

but the emitted `aud` claim is the API application client ID without the `api://` prefix:

```bash
export EXTERNAL_IDP_ALLOWED_AUDIENCES="<runtime-api-client-id>"
```

Do not use AgentCore `allowedClients` for Entra v2 client-credentials tokens. AgentCore validates `allowedClients` against a token claim named `client_id`, while Entra v2 emits the client app ID in `azp`. Use `customClaims` instead:

```bash
export AGENTCORE_EXTERNAL_JWT_CLIENT_CLAIM_NAME="azp"
export AGENTCORE_EXTERNAL_JWT_SCOPE_CLAIM_NAME="roles"
export AGENTCORE_EXTERNAL_JWT_SCOPE_CLAIM_VALUE_TYPE="STRING_ARRAY"
```
