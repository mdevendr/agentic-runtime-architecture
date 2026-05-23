# Runtime Inbound Identity and Trust Boundaries

This pattern proves the Stage 4 identity and trust boundary for Amazon Bedrock AgentCore Runtime. It covers both direct inbound Runtime invocation and Gateway-fronted Runtime invocation:

```text
Caller -> AgentCore Runtime
Caller -> AgentCore Gateway -> AgentCore Runtime target
```

The core architectural question is whose identity Runtime evaluates at the final authorization boundary.

## Implemented Scope

| # | Scenario | Source boundary | Auth/identity | Status |
|---|---|---|---|---|
| 1 | `client_runtime_iam_role` | Direct client -> Runtime | IAM role, SigV4 | Implemented |
| 2 | `client_runtime_iam_user` | Direct client -> Runtime | IAM user, SigV4 | Implemented |
| 3 | `client_runtime_identity_center` | Direct federated human -> Runtime | IAM Identity Center role, SigV4 | Implemented |
| 4 | `workload_lambda_iam_role` | Lambda workload -> Runtime | Lambda execution role, SigV4 | Implemented |
| 5 | `client_runtime_oauth_client` | Direct client -> Cognito -> Runtime | OAuth client credentials bearer token | Implemented |
| 6 | `api_gateway_lambda_oidc_jwt` | Browser -> Cognito/Google -> API Gateway/Lambda -> Runtime | OIDC/JWT user token | Implemented, manual browser proof |
| 7 | `external_idp_direct_jwt` | External IdP client -> Runtime | non-Cognito JWT bearer token | Implemented |
| 8 | `private_network_lambda_runtime` | Private Lambda -> Private Runtime | private network path plus Runtime auth | Implemented |

Mentioned but not implemented separately:

- ECS task role, EKS Pod Identity/IRSA, and EC2 instance profile: same Runtime IAM/SigV4 pattern as Lambda workload.
- GitHub Actions OIDC into AWS role: same Runtime IAM/SigV4 pattern once an AWS role is assumed.
- Cross-account IAM role: same Runtime IAM/SigV4 pattern unless cross-account is a real requirement.
- Gateway-fronted Runtime is covered as an expansion below: `Client -> AgentCore Gateway -> AgentCore Runtime target`. This is an alternate Runtime front door, not an MCP Gateway tool target.

## Gateway-Fronted Runtime Expansion

These scenarios cover Gateway as the front door and Runtime as the target behind the Gateway. They are separate from the existing MCP Gateway tool path, where Runtime calls Gateway to reach Lambda/API tools.

| # | Scenario | Gateway inbound auth | Runtime target credential provider | Runtime authorizes |
|---|---|---|---|---|
| G1 | `gateway_fronted_runtime_iam_sigv4` | `AWS_IAM` | `GATEWAY_IAM_ROLE` | Gateway service role |
| G2 | `gateway_fronted_runtime_caller_iam` | `AWS_IAM` | `CALLER_IAM_CREDENTIALS` | Original caller IAM identity |
| G3 | `gateway_fronted_runtime_oauth_jwt` | `AWS_IAM` | `OAUTH` | Gateway-obtained Cognito JWT |
| G4 | `gateway_fronted_runtime_jwt_passthrough` | `CUSTOM_JWT` | `JWT_PASSTHROUGH` | Original caller JWT |

Gateway is the front door. Runtime remains the target and final authorization boundary. G1 and G3 use Gateway-carried credentials at Runtime; G2 and G4 preserve caller-carried identity at Runtime. Secrets Manager is only part of G3, where AgentCore Identity stores the Cognito machine-client secret used to obtain OAuth tokens.

SDK/CLI note: boto3/botocore 1.43.14 exposes the Runtime target shape needed by the Python setup script. The global AWS CLI v2 may still lag, so use the Python setup script as the source of truth for this scenario. Runtime targets need a Gateway without protocol type and a target shape like:

```json
{
  "http": {
    "agentcoreRuntime": {
      "arn": "<runtime-arn>"
    }
  }
}
```

Run the first mode support check with:

```bash
python gateway_fronted_runtime/iam_sigv4_setup.py --check-only
```

Detailed Gateway-fronted Runtime runbooks are in `gateway_fronted_runtime/README.md`.

## Runtime Variants

AgentCore Runtime inbound authentication is configured when the runtime is created. IAM/SigV4 and JWT bearer scenarios should use separate Runtime variants.

| Runtime variant | Deploy command | Output ARN variable | Used by |
|---|---|---|---|
| `iam` | `python deploy_runtime.py --runtime-variant iam --scenario-name client_runtime_iam_role` | `AGENT_RUNTIME_ARN_IAM` | Scenarios 1, 2, 3, and 4 |
| `oauth-client` | `python deploy_runtime.py --runtime-variant oauth-client --scenario-name client_runtime_oauth_client` | `AGENT_RUNTIME_ARN_OAUTH_CLIENT` | Scenario 5 |
| `oidc-jwt` | `python deploy_runtime.py --runtime-variant oidc-jwt --scenario-name api_gateway_lambda_oidc_jwt` | `AGENT_RUNTIME_ARN_OIDC_JWT` | Scenario 6 |
| `external-jwt` | `python deploy_runtime.py --runtime-variant external-jwt --scenario-name external_idp_direct_jwt` | `AGENT_RUNTIME_ARN_EXTERNAL_JWT` | Scenario 7 |
| `iam-private` | `python deploy_runtime.py --runtime-variant iam-private --scenario-name private_network_lambda_runtime` | `AGENT_RUNTIME_ARN_IAM_PRIVATE` | Scenario 8 |

## Folder Layout

```text
identity_trust/
  identity_setup.py
  identity_center_setup.py
  identity_provider_setup.py
  workload_lambda_setup.py
  lambda_invoke_runtime_workload.py
  api_gateway_oidc_setup.py
  lambda_oidc_runtime_app.py
  external_idp_setup.py
  private_network_setup.py
  runtime_gateway_frontdoor_setup.py
  gateway_fronted_runtime/
    iam_sigv4_setup.py
    caller_iam_setup.py
    oauth_jwt_setup.py
    jwt_passthrough_setup.py
    invoke_target_http.py
    invoke_jwt_passthrough.py
  identity_tests.py
  scenarios/
    client_runtime_iam_role/
    client_runtime_iam_user/
    client_runtime_identity_center/
    workload_lambda_iam_role/
    client_runtime_oauth_client/
    api_gateway_lambda_oidc_jwt/
    external_idp_direct_jwt/
    private_network_lambda_runtime/
    gateway_fronted_runtime/
    gateway_fronted_runtime_iam_sigv4/
    gateway_fronted_runtime_caller_iam/
    gateway_fronted_runtime_oauth_jwt/
    gateway_fronted_runtime_jwt_passthrough/
  evidence/
```

Each scenario has:

- `scenario.json`: machine-readable source boundary, identity type, auth type, setup hints, and test list.
- `expected.md`: human-readable setup and evidence expectations.
- `evidence/<scenario>/<timestamp>/`: captured stdout, stderr, per-test result JSON, and scenario summary.

Read `SECURITY.md` before sharing outputs. JWTs, OAuth codes, client secrets, IAM keys, STS session tokens, and generated `.env` files are secret material. The scripts redact common token and secret patterns before writing evidence, but evidence should still be reviewed before it is copied outside the lab.

Detailed runbooks for the eight direct inbound Runtime scenarios are in `scenarios/README.md`.

## Evidence Snapshot

The latest passing evidence from the completed runs is:

| Scenario | Evidence |
|---|---|
| `client_runtime_iam_role` | `evidence/client_runtime_iam_role/20260521T113704Z/summary.json` |
| `workload_lambda_iam_role` | `evidence/workload_lambda_iam_role/20260521T130016Z/summary.json` |
| `client_runtime_oauth_client` | `evidence/client_runtime_oauth_client/20260521T141207Z/summary.json` |
| `external_idp_direct_jwt` | `evidence/external_idp_direct_jwt/20260521T161508Z/summary.json` |
| `private_network_lambda_runtime` | `evidence/private_network_lambda_runtime/20260521T175508Z/summary.json` |

`client_runtime_identity_center` has an allow-profile pass in `evidence/client_runtime_identity_center/20260521T125249Z/summary.json`; the full allow/deny automated run requires the local deny SSO profile to exist. `api_gateway_lambda_oidc_jwt` is proven through the browser callback page because it intentionally exercises an interactive Google login.

## Scenario 1: Direct Client IAM Role

Provisioning order:

```text
1. Create Gateway dependencies with setup_gateway.py.
2. Export the Gateway URL and ARN from setup output.
3. Deploy IAM Runtime with deploy_runtime.py --runtime-variant iam.
4. Export the Runtime ARN from deploy output.
5. Create IAM role client identities with identity_setup.py.
6. Export the allow and deny role ARNs from identity setup output.
7. Run identity_tests.py run-scenario for client_runtime_iam_role.
8. Review evidence/client_runtime_iam_role/<timestamp>/.
```

Commands:

```bash
cd identity_trust

export AWS_REGION="eu-west-2"
export BEDROCK_MODEL_ID="<bedrock-model-id-with-tool-use-support>"
export AGENTCORE_GATEWAY_AUTHORIZER="AWS_IAM"

python setup_gateway.py

export AGENTCORE_GATEWAY_URL="<gateway-url-from-setup-output>"
export AGENTCORE_GATEWAY_ARN="<gateway-arn-from-setup-output>"

python deploy_runtime.py --runtime-variant iam --scenario-name client_runtime_iam_role

export AGENT_RUNTIME_ARN="<runtime-arn-from-deploy-output>"
export AGENT_RUNTIME_ARN_IAM="<runtime-arn-from-deploy-output>"

python identity_setup.py

export PROMPT4_CLIENT_RUNTIME_IAM_ROLE_ALLOW_ARN="<allow-role-arn-from-identity-setup-output>"
export PROMPT4_CLIENT_RUNTIME_IAM_ROLE_DENY_ARN="<deny-role-arn-from-identity-setup-output>"

python identity_tests.py run-scenario scenarios/client_runtime_iam_role/scenario.json
```

## Scenario 2: Direct Client IAM User

This scenario reuses the IAM Runtime variant from scenario 1. The difference is the source identity:

```text
client process -> IAM user access key -> SigV4 signed InvokeAgentRuntime -> IAM Runtime
```

Provisioning order:

```text
1. Deploy or reuse the IAM Runtime with deploy_runtime.py --runtime-variant iam.
2. Export AGENT_RUNTIME_ARN.
3. Create IAM user client identities and access keys with identity_setup.py --include-iam-users.
4. Export the allow and deny IAM user key variables from identity setup output.
5. Run identity_tests.py run-scenario for client_runtime_iam_user.
6. Review evidence/client_runtime_iam_user/<timestamp>/.
```

Commands:

```bash
cd identity_trust

export AGENT_RUNTIME_ARN="<runtime-arn-from-deploy-output>"

python identity_setup.py --include-iam-users

export PROMPT4_CLIENT_RUNTIME_IAM_USER_ALLOW_ACCESS_KEY_ID="<allow-user-access-key-id>"
export PROMPT4_CLIENT_RUNTIME_IAM_USER_ALLOW_SECRET_ACCESS_KEY="<allow-user-secret-access-key>"
export PROMPT4_CLIENT_RUNTIME_IAM_USER_DENY_ACCESS_KEY_ID="<deny-user-access-key-id>"
export PROMPT4_CLIENT_RUNTIME_IAM_USER_DENY_SECRET_ACCESS_KEY="<deny-user-secret-access-key>"

python identity_tests.py run-scenario scenarios/client_runtime_iam_user/scenario.json
```

If you are not using default AWS credentials, pass the same profile you used for the role scenario:

```bash
python identity_setup.py --profile "<aws-profile>" --include-iam-users
python identity_tests.py run-scenario scenarios/client_runtime_iam_user/scenario.json
```

The setup script writes the key values to `identity_test_users.env`, which is gitignored. In Git Bash you can export everything from that file with:

```bash
set -a
source identity_test_users.env
set +a
```

AWS only returns a secret access key when the key is created. If these dedicated test users already have keys and you need a fresh local copy, explicitly rotate them:

```bash
python identity_setup.py --include-iam-users --rotate-iam-user-keys
```

The script does not print IAM user access keys or secret access keys to stdout. Source `identity_test_users.env` locally instead of copying the values into docs, chat, or evidence.

## Scenario 3: Direct Client IAM Identity Center

This scenario also reuses the IAM Runtime variant. The difference is that the client gets temporary AWS credentials through IAM Identity Center:

```text
human login -> IAM Identity Center permission set -> AWSReservedSSO role credentials -> SigV4 signed InvokeAgentRuntime -> IAM Runtime
```

Provisioning order:

```text
1. Deploy or reuse the IAM Runtime with deploy_runtime.py --runtime-variant iam.
2. Export AGENT_RUNTIME_ARN.
3. Create Identity Center allow and deny permission sets with identity_center_setup.py.
4. Configure local AWS CLI SSO profiles for each permission set.
5. Run aws sso login for the allow and deny profiles.
6. Run identity_tests.py run-scenario for client_runtime_identity_center.
7. Review evidence/client_runtime_identity_center/<timestamp>/.
```

Commands:

```bash
cd identity_trust

export AGENT_RUNTIME_ARN="<runtime-arn-from-deploy-output>"

python identity_center_setup.py --profile work --user melon

set -a
source identity_center.env
set +a
```

The setup creates:

- `Prompt4RuntimeInvokeAllow`: allows `bedrock-agentcore:InvokeAgentRuntime` on the Runtime and its endpoints.
- `Prompt4RuntimeInvokeDeny`: explicitly denies `bedrock-agentcore:InvokeAgentRuntime` on the Runtime and its endpoints.
- Account assignments for the configured Identity Center user.

Then configure two AWS CLI SSO profiles. Use the IAM Identity Center start URL from the AWS console, the account ID `211125489043`, and these role names:

```text
Prompt4RuntimeInvokeAllow
Prompt4RuntimeInvokeDeny
```

Recommended profile names, matching `scenario.json`:

```text
prompt4-identity-center-allow
prompt4-identity-center-deny
```

After the profiles exist:

```bash
aws sso login --profile prompt4-identity-center-allow
aws sso login --profile prompt4-identity-center-deny

python identity_tests.py run-scenario scenarios/client_runtime_identity_center/scenario.json
```

## Scenario 4: Lambda Workload IAM Role

This scenario proves an AWS workload source boundary. The local machine only invokes Lambda; the Runtime call itself is made by the Lambda execution role:

```text
local tester -> Lambda InvokeFunction -> Lambda execution role -> SigV4 signed InvokeAgentRuntime -> IAM Runtime
```

Provisioning order:

```text
1. Deploy or reuse the IAM Runtime with deploy_runtime.py --runtime-variant iam.
2. Export AGENT_RUNTIME_ARN_IAM.
3. Create allow and deny Lambda workload functions with workload_lambda_setup.py.
4. Source workload_lambda.env.
5. Run identity_tests.py run-scenario for workload_lambda_iam_role.
6. Review evidence/workload_lambda_iam_role/<timestamp>/.
```

Commands:

```bash
cd identity_trust

export AGENT_RUNTIME_ARN_IAM="<runtime-arn-from-deploy-output>"

python workload_lambda_setup.py --profile work

set -a
source workload_lambda.env
set +a

python identity_tests.py run-scenario scenarios/workload_lambda_iam_role/scenario.json
```

The setup creates:

- `prompt4-runtime-workload-allow`: Lambda function whose role can invoke Runtime.
- `prompt4-runtime-workload-deny`: Lambda function whose role is explicitly denied.
- `Prompt4LambdaRuntimeWorkloadAllow`: Lambda execution role with Runtime invoke allow.
- `Prompt4LambdaRuntimeWorkloadDeny`: Lambda execution role with Runtime invoke deny.

## Cognito Setup For Scenarios 5 And 6

Create Cognito resources:

```bash
python identity_provider_setup.py
```

This creates or updates:

- Cognito User Pool: `prompt4-client-runtime-idp`
- Resource server: `agentcore-runtime`
- Scope: `agentcore-runtime/invoke`
- User app client: `prompt4-user-client`
- Machine app client: `prompt4-machine-client`
- Hosted UI domain prefix
- Optional Google IdP when `PROMPT4_GOOGLE_CLIENT_ID` and `PROMPT4_GOOGLE_CLIENT_SECRET` are set

OAuth client Runtime authorizer inputs:

```bash
export AGENTCORE_OAUTH_CLIENT_DISCOVERY_URL="https://cognito-idp.<region>.amazonaws.com/<user-pool-id>/.well-known/openid-configuration"
export AGENTCORE_OAUTH_CLIENT_ALLOWED_CLIENTS="<machine-app-client-id>"
export AGENTCORE_OAUTH_CLIENT_ALLOWED_SCOPES="agentcore-runtime/invoke"
```

OIDC/JWT Runtime authorizer inputs:

```bash
export AGENTCORE_OIDC_JWT_DISCOVERY_URL="https://cognito-idp.<region>.amazonaws.com/<user-pool-id>/.well-known/openid-configuration"
export AGENTCORE_OIDC_JWT_ALLOWED_CLIENTS="<user-app-client-id>"
export AGENTCORE_OIDC_JWT_ALLOWED_SCOPES="agentcore-runtime/invoke"
```

## Scenario 5: Direct Client OAuth Client

This scenario uses Cognito as an OAuth authorization server for machine-to-machine access. No Cognito user is used:

```text
client app -> Cognito /oauth2/token -> access token -> OAuth Runtime
```

Provisioning order:

```text
1. Create Cognito resources with identity_provider_setup.py.
2. Source identity_provider.env.
3. Deploy the OAuth Runtime with deploy_runtime.py --runtime-variant oauth-client.
4. Export AGENT_RUNTIME_ARN_OAUTH_CLIENT.
5. Run identity_tests.py run-scenario for client_runtime_oauth_client.
6. Review evidence/client_runtime_oauth_client/<timestamp>/.
```

Commands:

```bash
cd identity_trust

set -a
source identity_provider.env
set +a

python deploy_runtime.py --runtime-variant oauth-client --scenario-name client_runtime_oauth_client

export AGENT_RUNTIME_ARN_OAUTH_CLIENT="<runtime-arn-from-deploy-output>"

python identity_tests.py run-scenario scenarios/client_runtime_oauth_client/scenario.json
```

The test runner requests a Cognito client-credentials access token using:

- `PROMPT4_COGNITO_TOKEN_URL`
- `PROMPT4_COGNITO_MACHINE_CLIENT_ID`
- `PROMPT4_COGNITO_MACHINE_CLIENT_SECRET`
- `PROMPT4_COGNITO_SCOPE`

Then it invokes the OAuth Runtime with:

```text
Authorization: Bearer <access-token>
```

Do not print or store the actual access token. It is a bearer credential.

## Scenario 6: API Gateway And Lambda OIDC/JWT

This scenario uses a real browser login. Cognito/Google issues a user access token, API Gateway receives the application callback, and Lambda forwards the user token to the OIDC Runtime:

```text
browser -> Cognito managed login -> Google -> API Gateway /callback -> Lambda -> OIDC Runtime
```

Provisioning order:

```text
1. Source identity_provider.env.
2. Deploy the OIDC Runtime with deploy_runtime.py --runtime-variant oidc-jwt.
3. Export AGENT_RUNTIME_ARN_OIDC_JWT.
4. Create API Gateway and callback Lambda with api_gateway_oidc_setup.py.
5. Open PROMPT4_OIDC_START_URL in a browser.
6. Complete Google login.
7. Confirm the callback result shows runtime_status_code=200.
```

Commands:

```bash
cd identity_trust

set -a
source identity_provider.env
set +a

python deploy_runtime.py --runtime-variant oidc-jwt --scenario-name api_gateway_lambda_oidc_jwt

export AGENT_RUNTIME_ARN_OIDC_JWT="<runtime-arn-from-deploy-output>"

python api_gateway_oidc_setup.py --profile work

set -a
source api_gateway_oidc.env
set +a
```

The setup updates the Cognito user app client with the generated API Gateway callback URL. The generated start URL redirects straight to Google through Cognito:

```bash
echo "$PROMPT4_OIDC_START_URL"
```

## External IdP Requirements

For `external_idp_direct_jwt`, pick a real non-Cognito provider such as Okta, Microsoft Entra ID, or Auth0. Required values:

```bash
export EXTERNAL_IDP_DISCOVERY_URL="https://<issuer>/.well-known/openid-configuration"
export EXTERNAL_IDP_ALLOWED_CLIENTS="<client-id>"
export EXTERNAL_IDP_ALLOWED_AUDIENCES="<audience-or-api-identifier>"
export EXTERNAL_IDP_ALLOWED_SCOPES="<required-scope>"
export EXTERNAL_IDP_TOKEN_URL="<token-endpoint>"
export EXTERNAL_IDP_CLIENT_ID="<client-id>"
export EXTERNAL_IDP_CLIENT_SECRET="<client-secret>"
export EXTERNAL_IDP_TOKEN_SCOPE="<token-request-scope>"
```

Run the plan/check helper:

```bash
python external_idp_setup.py
```

## Scenario 7: External IdP Direct JWT

This scenario removes Cognito from the inbound Runtime trust path. The client gets a JWT directly from an external IdP, then sends it to Runtime:

```text
client app -> Microsoft Entra token endpoint -> access token -> external-jwt Runtime
```

For the current Entra setup:

```bash
export EXTERNAL_IDP_DISCOVERY_URL="https://login.microsoftonline.com/7678fe05-e289-485a-a9ed-3e44c3d5b087/v2.0/.well-known/openid-configuration"
export EXTERNAL_IDP_TOKEN_URL="https://login.microsoftonline.com/7678fe05-e289-485a-a9ed-3e44c3d5b087/oauth2/v2.0/token"
export EXTERNAL_IDP_CLIENT_ID="dc5de7f0-756e-49c1-9947-ec167e698e13"
export EXTERNAL_IDP_CLIENT_SECRET="<client-secret>"
export EXTERNAL_IDP_ALLOWED_CLIENTS="dc5de7f0-756e-49c1-9947-ec167e698e13"
export EXTERNAL_IDP_ALLOWED_AUDIENCES="4aa3a52d-a3ee-4031-8db4-1b2261bbab11"
export EXTERNAL_IDP_ALLOWED_SCOPES="Runtime.Invoke"
export EXTERNAL_IDP_TOKEN_SCOPE="api://4aa3a52d-a3ee-4031-8db4-1b2261bbab11/.default"
```

Map those values to Runtime authorizer inputs:

```bash
export AGENTCORE_EXTERNAL_JWT_DISCOVERY_URL="$EXTERNAL_IDP_DISCOVERY_URL"
export AGENTCORE_EXTERNAL_JWT_ALLOWED_CLIENTS="$EXTERNAL_IDP_ALLOWED_CLIENTS"
export AGENTCORE_EXTERNAL_JWT_ALLOWED_AUDIENCES="$EXTERNAL_IDP_ALLOWED_AUDIENCES"
export AGENTCORE_EXTERNAL_JWT_ALLOWED_SCOPES="$EXTERNAL_IDP_ALLOWED_SCOPES"
export AGENTCORE_EXTERNAL_JWT_CLIENT_CLAIM_NAME="azp"
export AGENTCORE_EXTERNAL_JWT_SCOPE_CLAIM_NAME="roles"
export AGENTCORE_EXTERNAL_JWT_SCOPE_CLAIM_VALUE_TYPE="STRING_ARRAY"
```

The Entra v2 client-credentials token does not emit `client_id` or `scope`. It emits the calling application in `azp`, the application permission in `roles`, and the audience as the API application client ID without the `api://` prefix. Runtime therefore uses:

- `allowedAudience`: `4aa3a52d-a3ee-4031-8db4-1b2261bbab11`
- custom claim `azp`: `dc5de7f0-756e-49c1-9947-ec167e698e13`
- custom claim `roles`: contains `Runtime.Invoke`

The token request still uses the `.default` scope with the `api://` prefix:

```bash
export EXTERNAL_IDP_TOKEN_SCOPE="api://4aa3a52d-a3ee-4031-8db4-1b2261bbab11/.default"
```

Deploy and test:

```bash
python external_idp_setup.py
python deploy_runtime.py --runtime-variant external-jwt --scenario-name external_idp_direct_jwt

export AGENT_RUNTIME_ARN_EXTERNAL_JWT="<runtime-arn-from-deploy-output>"

python identity_tests.py run-scenario scenarios/external_idp_direct_jwt/scenario.json
```

If a failed Runtime with the same name already exists, deploy with a unique scenario name and export the new ARN:

```bash
python deploy_runtime.py --runtime-variant external-jwt --scenario-name external_idp_direct_jwt_entra_aud
```

## Scenario 8: Private Lambda And Private Runtime

This scenario layers private network controls on top of Runtime IAM authentication:

```text
VPC Lambda -> AgentCore PrivateLink endpoint -> VPC-mode Runtime
```

Private connectivity proves network reachability. The allow/deny Lambda roles prove Runtime auth still applies.

Provisioning order:

```text
1. Create/reuse VPC endpoint infrastructure with private_network_setup.py.
2. Source private_network.env.
3. Deploy the VPC-mode Runtime with deploy_runtime.py --runtime-variant iam-private.
4. Create VPC-attached allow/deny Lambda functions with workload_lambda_setup.py.
5. Run identity_tests.py run-scenario for private_network_lambda_runtime.
```

Commands:

```bash
python private_network_setup.py --profile work

set -a
source private_network.env
set +a

python deploy_runtime.py --runtime-variant iam-private --scenario-name private_network_lambda_runtime

export AGENT_RUNTIME_ARN_IAM_PRIVATE="<runtime-arn-from-deploy-output>"

python workload_lambda_setup.py \
  --profile work \
  --runtime-arn "$AGENT_RUNTIME_ARN_IAM_PRIVATE" \
  --allow-function-name prompt4-private-runtime-workload-allow \
  --deny-function-name prompt4-private-runtime-workload-deny \
  --allow-role-name Prompt4PrivateLambdaRuntimeWorkloadAllow \
  --deny-role-name Prompt4PrivateLambdaRuntimeWorkloadDeny \
  --env-file private_workload_lambda.env

export PROMPT4_PRIVATE_LAMBDA_ALLOW_FUNCTION_NAME="prompt4-private-runtime-workload-allow"
export PROMPT4_PRIVATE_LAMBDA_DENY_FUNCTION_NAME="prompt4-private-runtime-workload-deny"

python identity_tests.py run-scenario scenarios/private_network_lambda_runtime/scenario.json
```

Private endpoint services used:

- `com.amazonaws.eu-west-2.bedrock-agentcore`
- `com.amazonaws.eu-west-2.bedrock-agentcore.gateway`
- `com.amazonaws.eu-west-2.bedrock-runtime`
- `com.amazonaws.eu-west-2.ecr.api`
- `com.amazonaws.eu-west-2.ecr.dkr`
- `com.amazonaws.eu-west-2.logs`
- S3 gateway endpoint for ECR image layer access

The current setup uses default endpoint policies. That proves the private path and Runtime authorization behaviour. A production hardening pass should add endpoint policies that restrict the endpoint to the required principals, resources, and actions.

## Scenario Runner

Scenario mode:

```bash
python identity_tests.py run-scenario scenarios/client_runtime_iam_role/scenario.json
```

## Local Validation

```bash
python -m py_compile main.py setup_gateway.py deploy_runtime.py invoke_runtime.py identity_setup.py identity_center_setup.py identity_provider_setup.py workload_lambda_setup.py lambda_invoke_runtime_workload.py api_gateway_oidc_setup.py lambda_oidc_runtime_app.py external_idp_setup.py private_network_setup.py identity_tests.py lambda_calculate_order_total.py lambda_check_refund_eligibility.py
python -m json.tool tool_schema.json
python -m json.tool scenarios/client_runtime_iam_role/scenario.json
python -m json.tool scenarios/client_runtime_iam_user/scenario.json
python -m json.tool scenarios/client_runtime_identity_center/scenario.json
python -m json.tool scenarios/workload_lambda_iam_role/scenario.json
python -m json.tool scenarios/client_runtime_oauth_client/scenario.json
python -m json.tool scenarios/api_gateway_lambda_oidc_jwt/scenario.json
python -m json.tool scenarios/external_idp_direct_jwt/scenario.json
python -m json.tool scenarios/private_network_lambda_runtime/scenario.json
python -m json.tool scenarios/gateway_fronted_runtime/scenario.json
```

Private network is not identity by itself. It constrains where calls can come from; Runtime authentication still decides who is allowed.
