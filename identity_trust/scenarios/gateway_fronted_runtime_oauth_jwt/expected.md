# gateway_fronted_runtime_oauth_jwt

This scenario implements the third Gateway-fronted Runtime authorization mode:

```text
Client -> AgentCore Gateway -> AgentCore Runtime target
```

For this mode, Gateway obtains an OAuth access token from an AgentCore Identity OAuth2 credential provider. Runtime authorizes the JWT bearer token, not the caller IAM identity and not the Gateway service role.

Expected trust chain:

```text
1. Client signs request to Gateway target URL with caller IAM credentials.
2. Gateway accepts the inbound request through its AWS_IAM authorizer.
3. Gateway resolves the Runtime target.
4. Gateway asks AgentCore Identity credential provider for an OAuth access token.
5. AgentCore Identity uses the configured Cognito machine client credentials.
6. Cognito returns a JWT access token.
7. Gateway calls Runtime with Authorization: Bearer <token>.
8. Runtime JWT authorizer validates issuer, JWKS signature, exp, client/app claim, audience, and scope.
9. Runtime invokes the hosted agent application if the token is valid.
```

Target credential provider:

```json
{
  "credentialProviderType": "OAUTH",
  "credentialProvider": {
    "oauthCredentialProvider": {
      "providerArn": "<agentcore-oauth2-credential-provider-arn>",
      "scopes": ["agentcore-runtime/invoke"],
      "grantType": "CLIENT_CREDENTIALS"
    }
  }
}
```

Setup command:

```bash
python gateway_fronted_runtime/oauth_jwt_setup.py --runtime-arn "$AGENT_RUNTIME_ARN_OAUTH_CLIENT"
```

Test command:

```bash
python gateway_fronted_runtime/invoke_target_http.py --env-file gateway_fronted_runtime_oauth_jwt.env
```

Gateway service role permissions required for token retrieval:

```text
bedrock-agentcore:GetWorkloadAccessToken
bedrock-agentcore:GetResourceOauth2Token
secretsmanager:GetSecretValue
```

