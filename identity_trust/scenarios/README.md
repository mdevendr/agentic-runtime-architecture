# Runtime Inbound Scenario Runbooks

These scenarios test callers crossing the inbound trust boundary into AgentCore Runtime:

```text
source -> AgentCore Runtime
```

Gateway-fronted Runtime scenarios live separately under `../gateway_fronted_runtime/README.md` because the boundary changes to:

```text
source -> AgentCore Gateway -> AgentCore Runtime target
```

## Scenario Groups

| # | Scenario | Runtime auth | Main setup script |
|---|---|---|---|
| 1 | `client_runtime_iam_role` | IAM/SigV4 | `identity_setup.py` |
| 2 | `client_runtime_iam_user` | IAM/SigV4 | `identity_setup.py --include-iam-users` |
| 3 | `client_runtime_identity_center` | IAM/SigV4 via SSO credentials | `identity_center_setup.py` |
| 4 | `workload_lambda_iam_role` | Lambda execution role, IAM/SigV4 | `workload_lambda_setup.py` |
| 5 | `client_runtime_oauth_client` | Cognito OAuth client credentials JWT | `identity_provider_setup.py` |
| 6 | `api_gateway_lambda_oidc_jwt` | Cognito/Google user OIDC JWT | `identity_provider_setup.py`, `api_gateway_oidc_setup.py` |
| 7 | `external_idp_direct_jwt` | External IdP JWT, tested with Microsoft Entra ID | `external_idp_setup.py` |
| 8 | `private_network_lambda_runtime` | Private network path plus IAM/SigV4 | `private_network_setup.py`, `workload_lambda_setup.py` |

## Shared Runtime Variants

| Runtime variant | Used by | Deploy command |
|---|---|---|
| `iam` | Scenarios 1, 2, 3, 4 | `python deploy_runtime.py --runtime-variant iam --scenario-name client_runtime_iam_role` |
| `oauth-client` | Scenario 5 | `python deploy_runtime.py --runtime-variant oauth-client --scenario-name client_runtime_oauth_client` |
| `oidc-jwt` | Scenario 6 | `python deploy_runtime.py --runtime-variant oidc-jwt --scenario-name api_gateway_lambda_oidc_jwt` |
| `external-jwt` | Scenario 7 | `python deploy_runtime.py --runtime-variant external-jwt --scenario-name external_idp_direct_jwt` |
| `iam-private` | Scenario 8 | `python deploy_runtime.py --runtime-variant iam-private --scenario-name private_network_lambda_runtime` |

## 1. Direct Client IAM Role

```text
client -> assume IAM role -> SigV4 InvokeAgentRuntime -> Runtime
```

Provisioning sequence:

```text
1. Run setup_gateway.py for the runtime's outbound MCP tool dependency.
2. Deploy IAM Runtime.
3. Run identity_setup.py.
4. Export allow and deny role ARNs.
5. Run identity_tests.py for client_runtime_iam_role.
```

Key commands:

```bash
python setup_gateway.py
python deploy_runtime.py --runtime-variant iam --scenario-name client_runtime_iam_role
python identity_setup.py
python identity_tests.py run-scenario scenarios/client_runtime_iam_role/scenario.json
```

## 2. Direct Client IAM User

```text
client -> IAM user access keys -> SigV4 InvokeAgentRuntime -> Runtime
```

Key commands:

```bash
python identity_setup.py --include-iam-users
set -a
source identity_test_users.env
set +a
python identity_tests.py run-scenario scenarios/client_runtime_iam_user/scenario.json
```

IAM user secrets are written to `identity_test_users.env`. Do not copy them into docs, chat, or evidence.

## 3. IAM Identity Center

```text
human -> IAM Identity Center -> AWSReservedSSO role credentials -> SigV4 InvokeAgentRuntime -> Runtime
```

Key commands:

```bash
python identity_center_setup.py --profile work --user melon
set -a
source identity_center.env
set +a
aws sso login --profile prompt4-identity-center-allow
aws sso login --profile prompt4-identity-center-deny
python identity_tests.py run-scenario scenarios/client_runtime_identity_center/scenario.json
```

The permission sets are:

- `Prompt4RuntimeInvokeAllow`
- `Prompt4RuntimeInvokeDeny`

## 4. Lambda Workload IAM Role

```text
local tester -> Lambda -> Lambda execution role -> SigV4 InvokeAgentRuntime -> Runtime
```

Key commands:

```bash
python workload_lambda_setup.py --profile work
set -a
source workload_lambda.env
set +a
python identity_tests.py run-scenario scenarios/workload_lambda_iam_role/scenario.json
```

## 5. Cognito OAuth Client Credentials

```text
machine client -> Cognito /oauth2/token -> access token -> Runtime JWT authorizer
```

Key commands:

```bash
python identity_provider_setup.py
set -a
source identity_provider.env
set +a
python deploy_runtime.py --runtime-variant oauth-client --scenario-name client_runtime_oauth_client
python identity_tests.py run-scenario scenarios/client_runtime_oauth_client/scenario.json
```

No Cognito user is needed for this scenario. The machine app client gets a bearer access token using client credentials.

## 6. Browser OIDC Through API Gateway And Lambda

```text
browser -> Cognito managed login -> Google -> API Gateway /callback -> Lambda -> Runtime JWT authorizer
```

Key commands:

```bash
set -a
source identity_provider.env
set +a
python deploy_runtime.py --runtime-variant oidc-jwt --scenario-name api_gateway_lambda_oidc_jwt
python api_gateway_oidc_setup.py --profile work
set -a
source api_gateway_oidc.env
set +a
echo "$PROMPT4_OIDC_START_URL"
```

This one is proven through the browser callback because it intentionally exercises a real Google login.

## 7. External IdP Direct JWT

```text
client -> Microsoft Entra token endpoint -> Entra access token -> Runtime JWT authorizer
```

For Entra client credentials, Runtime uses:

- issuer discovery/JWKS from Entra v2
- audience as the API application client ID
- `azp` as the calling client app claim
- `roles` containing `Runtime.Invoke`

Key commands:

```bash
python external_idp_setup.py
python deploy_runtime.py --runtime-variant external-jwt --scenario-name external_idp_direct_jwt
python identity_tests.py run-scenario scenarios/external_idp_direct_jwt/scenario.json
```

## 8. Private Lambda And Private Runtime

```text
VPC Lambda -> PrivateLink/VPC endpoints -> VPC-mode Runtime
```

Key commands:

```bash
python private_network_setup.py --profile work
set -a
source private_network.env
set +a
python deploy_runtime.py --runtime-variant iam-private --scenario-name private_network_lambda_runtime
python workload_lambda_setup.py --profile work --runtime-arn "$AGENT_RUNTIME_ARN_IAM_PRIVATE" --env-file private_workload_lambda.env
python identity_tests.py run-scenario scenarios/private_network_lambda_runtime/scenario.json
```

Private network is not identity by itself. It narrows where calls can come from; Runtime authentication still decides who is allowed.

## Scenario Runner

```bash
python identity_tests.py run-scenario scenarios/client_runtime_iam_role/scenario.json
```

