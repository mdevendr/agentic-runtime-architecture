# gateway_fronted_runtime_jwt_passthrough

This scenario implements the fourth Gateway-fronted Runtime authorization mode:

```text
Client -> AgentCore Gateway -> AgentCore Runtime target
```

For this mode, the client already has a JWT access token. Gateway validates the inbound token and passes the same bearer token through to Runtime.

Expected trust chain:

```text
1. Client obtains Cognito JWT access token.
2. Client calls Gateway target URL with Authorization: Bearer <token>.
3. Gateway CUSTOM_JWT authorizer validates the inbound JWT.
4. Gateway routes to the Runtime target.
5. Gateway forwards the same Authorization bearer token.
6. Runtime JWT authorizer validates the same token.
7. Runtime invokes the hosted agent application if the token is valid.
```

Target credential provider:

```json
[
  {
    "credentialProviderType": "JWT_PASSTHROUGH"
  }
]
```

Setup command:

```bash
python gateway_fronted_runtime/jwt_passthrough_setup.py --runtime-arn "$AGENT_RUNTIME_ARN_OAUTH_CLIENT"
```

Test command:

```bash
python gateway_fronted_runtime/invoke_jwt_passthrough.py
```

