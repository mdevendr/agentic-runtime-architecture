# client_runtime_oauth_client

Fifth Client -> Runtime scenario.

Expected proof:

- Valid OAuth client credentials access token invokes Runtime.
- Wrong client secret fails at token issuance.
- Missing token fails at Runtime.
- Token missing required scope fails.
- Token for wrong audience/resource fails.

Use a separate Cognito app client from the OIDC/JWT user scenario because Cognito does not combine client credentials with user interactive grants in the same app client.

Runtime variant:

```bash
export AGENTCORE_OAUTH_CLIENT_DISCOVERY_URL="https://cognito-idp.<region>.amazonaws.com/<user-pool-id>/.well-known/openid-configuration"
export AGENTCORE_OAUTH_CLIENT_ALLOWED_CLIENTS="<machine-app-client-id>"
export AGENTCORE_OAUTH_CLIENT_ALLOWED_AUDIENCES="<optional-audience-values>"
export AGENTCORE_OAUTH_CLIENT_ALLOWED_SCOPES="<required-client-credentials-scopes>"

python deploy_runtime.py --runtime-variant oauth-client

export AGENT_RUNTIME_ARN_OAUTH_CLIENT="<runtime-arn-from-deploy-output>"
```

Test sequence:

1. Source `identity_provider.env`.
2. Deploy the `oauth-client` Runtime variant.
3. Export `AGENT_RUNTIME_ARN_OAUTH_CLIENT`.
4. Run `identity_tests.py run-scenario scenarios/client_runtime_oauth_client/scenario.json`.

The implemented automated proof covers valid token success and missing-token denial. Wrong secret and wrong scope are token issuance failures at Cognito, so they can be validated independently without invoking Runtime.
