# api_gateway_lambda_oidc_jwt

Sixth scenario.

Expected proof:

- Browser user authenticates with Google through Cognito.
- API Gateway/Lambda represents the application callback boundary.
- Lambda invokes the OIDC/JWT Runtime with a bearer token.
- Missing, expired, wrong-audience, and missing-scope tokens are rejected.

This replaces the earlier direct-client OIDC idea because real user login needs an application callback boundary.

Setup sequence:

1. Source `identity_provider.env`.
2. Deploy the `oidc-jwt` Runtime variant.
3. Export `AGENT_RUNTIME_ARN_OIDC_JWT`.
4. Run `api_gateway_oidc_setup.py`.
5. Open `PROMPT4_OIDC_START_URL` in a browser.
6. Complete Google login through Cognito.
7. Confirm the callback page shows `runtime_status_code=200`.

The setup creates:

- HTTP API route `GET /start`
- HTTP API route `GET /callback`
- Lambda function `prompt4-oidc-runtime-callback`
- Lambda role `Prompt4OidcRuntimeCallbackRole`
- Cognito user app client callback URL for the API Gateway callback
