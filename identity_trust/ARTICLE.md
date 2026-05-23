# Prompt 4: AgentCore Runtime Identity And Trust Boundaries

The earlier patterns in this repository focus on where tool execution happens. Prompt 4 asks a different question:

```text
Who is allowed to invoke the hosted agent runtime?
```

The boundary being tested is the inbound trust boundary to Amazon Bedrock AgentCore Runtime. The hosted agent still has its own outbound tool and gateway relationships, but this lab concentrates on the caller-to-runtime edge.

## Final Scenario Set

The implementation covers eight source combinations:

| # | Scenario | Source | Runtime auth model |
|---|---|---|---|
| 1 | `client_runtime_iam_role` | Direct client assuming an IAM role | IAM/SigV4 |
| 2 | `client_runtime_iam_user` | Direct client using IAM user access keys | IAM/SigV4 |
| 3 | `client_runtime_identity_center` | Human federated through IAM Identity Center | IAM/SigV4 |
| 4 | `workload_lambda_iam_role` | Lambda workload execution role | IAM/SigV4 |
| 5 | `client_runtime_oauth_client` | Machine client using Cognito client credentials | JWT bearer |
| 6 | `api_gateway_lambda_oidc_jwt` | Browser user through Cognito and Google | JWT bearer |
| 7 | `external_idp_direct_jwt` | Machine client using Microsoft Entra ID directly | JWT bearer |
| 8 | `private_network_lambda_runtime` | VPC Lambda invoking VPC-mode Runtime | Private network plus IAM/SigV4 |

Other options are deliberately discussed but not implemented separately:

- ECS task role, EKS Pod Identity/IRSA, and EC2 instance profile follow the same AWS workload IAM/SigV4 pattern as Lambda.
- GitHub Actions OIDC into AWS becomes the same Runtime IAM/SigV4 pattern after GitHub assumes an AWS role.
- Cross-account IAM role is valuable when account boundaries matter, but the Runtime authorization mechanics are still IAM/SigV4.

The Gateway-fronted Runtime expansion adds a second shape:

```text
caller -> AgentCore Gateway -> AgentCore Runtime target
```

That path does not replace Runtime authorization. Gateway is the front door, but Runtime remains the target and final authorization boundary.

## What Changed In The Runtime

AgentCore Runtime inbound authentication is configured when the Runtime is created. That means the lab uses separate Runtime variants:

- IAM Runtime for direct AWS callers and Lambda workload callers.
- OAuth client Runtime for Cognito machine-to-machine tokens.
- OIDC/JWT Runtime for browser user tokens issued by Cognito after Google login.
- External JWT Runtime for Microsoft Entra ID tokens without Cognito in the path.
- VPC-mode IAM Runtime for private Lambda to private Runtime testing.

The important design lesson is that caller identity and network placement are separate controls. A private network path reduces where calls can originate, but Runtime authentication still decides whether the caller is allowed.

## Execution Sequence

The recommended order is:

1. Create shared Gateway dependencies with `setup_gateway.py`.
2. Deploy the IAM Runtime and prove direct IAM role access.
3. Reuse the IAM Runtime for IAM user and IAM Identity Center access.
4. Add Lambda workload roles to prove AWS workload identity.
5. Create Cognito resources and test OAuth client credentials.
6. Add API Gateway/Lambda callback handling for browser OIDC through Google.
7. Configure Microsoft Entra ID and deploy the direct external JWT Runtime.
8. Create private VPC endpoints, deploy a VPC-mode Runtime, and test private Lambda access.

This sequence keeps the simplest AWS-native identity path first, then adds federation, browser login, external IdP trust, and private networking.

## Scenario Notes

### 1. Direct Client IAM Role

The client assumes an IAM role and signs `InvokeAgentRuntime` with SigV4:

```text
client -> STS AssumeRole -> temporary credentials -> SigV4 InvokeAgentRuntime -> Runtime
```

The allow role has `bedrock-agentcore:InvokeAgentRuntime` on the Runtime and its endpoint. The deny role is explicitly denied. The Runtime itself does not need to know the role name as a separate setting; IAM authorization evaluates the signed request against the caller's policies.

### 2. Direct Client IAM User

This is the same Runtime auth model as the IAM role scenario, but the credential source is a long-lived IAM user access key:

```text
client -> IAM user access key -> SigV4 InvokeAgentRuntime -> Runtime
```

This pattern is useful for proving the boundary, but production systems should prefer temporary credentials over long-lived access keys.

### 3. IAM Identity Center Federated Role

Here the human authenticates through IAM Identity Center and receives temporary credentials for an AWSReservedSSO role:

```text
human login -> Identity Center permission set -> SSO role credentials -> SigV4 InvokeAgentRuntime -> Runtime
```

From Runtime's perspective, this is still IAM/SigV4. The difference is how the credentials are issued and governed.

### 4. Lambda Workload IAM Role

The local test invokes Lambda, but Lambda is the real caller to Runtime:

```text
tester -> Lambda InvokeFunction -> Lambda execution role -> SigV4 InvokeAgentRuntime -> Runtime
```

This proves workload identity. ECS task roles, EKS Pod Identity/IRSA, and EC2 instance profiles are the same Runtime-side pattern with different compute platforms.

### 5. Cognito OAuth Client Credentials

This scenario is machine-to-machine. No Cognito user is involved:

```text
client app -> Cognito /oauth2/token -> access token -> Runtime
```

Cognito provides a resource server identifier, scope, app client ID, app client secret, and token endpoint. Runtime validates the bearer token against the Cognito discovery document, allowed client, audience, and scope.

### 6. Browser OIDC Through Cognito And Google

This scenario represents a real user login. The browser does not call Runtime directly. API Gateway and Lambda own the application callback:

```text
browser -> API Gateway /start
API Gateway -> Lambda
Lambda -> 302 redirect to Cognito
Cognito -> Google login
Google -> Cognito /oauth2/idpresponse
Cognito -> API Gateway /callback
API Gateway -> Lambda
Lambda -> Cognito token endpoint
Lambda -> Runtime with bearer token
```

Google only needs to know the Cognito IdP response URL. Cognito knows the application callback URL, which is the API Gateway callback route. That separation is why this scenario uses API Gateway/Lambda instead of a local direct client.

### 7. Microsoft Entra ID Direct JWT

This removes Cognito from the Runtime trust path:

```text
client app -> Entra token endpoint -> access token -> Runtime
```

The tested Entra client-credentials token had these important claims:

- `iss`: `https://login.microsoftonline.com/<tenant-id>/v2.0`
- `aud`: the Runtime API application client ID, without `api://`
- `azp`: the client application ID
- `roles`: contains `Runtime.Invoke`
- `ver`: `2.0`

The token request still uses `api://<runtime-api-client-id>/.default`, but Runtime must validate the emitted `aud` claim as `<runtime-api-client-id>`. Because Entra v2 client-credentials tokens do not emit `client_id` or `scp`, the Runtime authorizer uses custom claim validation:

- `azp` equals the allowed client application ID.
- `roles` contains `Runtime.Invoke`.

### 8. Private Lambda To Private Runtime

Private networking adds a network boundary:

```text
VPC Lambda -> VPC endpoints -> VPC-mode Runtime
```

The passing setup required endpoints not only for AgentCore, but also for platform dependencies used during private runtime startup and logging:

- `bedrock-agentcore`
- `bedrock-agentcore.gateway`
- `bedrock-runtime`
- `ecr.api`
- `ecr.dkr`
- `logs`
- S3 gateway endpoint for ECR image layers

The lab uses default endpoint policies. Production hardening should restrict endpoint policies to the exact principals, Runtime resources, ECR repository, log groups, and actions required.

## Gateway-Fronted Runtime Modes

The Gateway-fronted Runtime scenarios test a different inbound path from the direct Runtime scenarios. They are also different from the MCP tools path, where Runtime calls Gateway to reach Lambda or API tools. Here, the caller reaches Gateway first and Gateway routes to a Runtime target.

| Mode | Gateway inbound auth | Runtime target credential provider | Runtime authorizes |
|---|---|---|---|
| G1 | `AWS_IAM` | `GATEWAY_IAM_ROLE` | Gateway service role |
| G2 | `AWS_IAM` | `CALLER_IAM_CREDENTIALS` | Original caller IAM identity |
| G3 | `AWS_IAM` | `OAUTH` | Gateway-obtained Cognito JWT |
| G4 | `CUSTOM_JWT` | `JWT_PASSTHROUGH` | Original caller JWT |

The key distinction is whether Runtime sees a Gateway-carried identity or the caller's own identity. G1 and G3 are Gateway-carried modes. In G1, Gateway uses its service role to SigV4-sign `InvokeAgentRuntime`; Runtime IAM authorization therefore depends on the Gateway role's `bedrock-agentcore:InvokeAgentRuntime` permissions. In G3, Gateway uses AgentCore Identity to obtain a Cognito OAuth access token, and Runtime validates that JWT.

G2 and G4 are caller-carried modes. In G2, Gateway authenticates an IAM caller and signs the Runtime request with caller IAM credentials, so Runtime IAM policies on the original caller still matter. In G4, Gateway validates a caller JWT with `CUSTOM_JWT` and passes the same bearer token through to Runtime. Runtime must be configured with an authorizer that matches that same issuer, audience or client claim, and scope or role claim.

Secrets Manager only appears in G3 because AgentCore Identity needs the Cognito machine-client secret to obtain OAuth tokens. G4 does not mint a replacement token; the Gateway service role is mostly trust and routing infrastructure, similar to G2.

## Evidence Summary

Passing evidence exists for:

| Scenario | Evidence |
|---|---|
| IAM role | `evidence/client_runtime_iam_role/20260521T113704Z/summary.json` |
| Lambda workload role | `evidence/workload_lambda_iam_role/20260521T130016Z/summary.json` |
| Cognito OAuth client | `evidence/client_runtime_oauth_client/20260521T141207Z/summary.json` |
| Entra direct JWT | `evidence/external_idp_direct_jwt/20260521T161508Z/summary.json` |
| Private Lambda/Runtime | `evidence/private_network_lambda_runtime/20260521T175508Z/summary.json` |

The Identity Center evidence currently proves the allow profile and records that the deny profile must exist locally before the full allow/deny run can pass. The browser OIDC scenario is manually proven by completing Google login and receiving a callback page with `runtime_status_code=200`.

## Production Hardening

The lab is intentionally proof-oriented. A production design should add:

- Redaction controls for JWTs, bearer headers, OAuth authorization codes, client secrets, IAM keys, and STS session tokens.
- Secrets Manager or another managed secret store instead of local environment files.
- Temporary credentials wherever possible.
- Access key rotation and removal for any IAM user test identities.
- Least-privilege IAM policies scoped to exact Runtime ARNs and endpoints.
- Endpoint policies for private connectivity.
- Structured audit logs for token validation failures, IAM denies, and Runtime invocation outcomes.
- Cleanup automation for temporary app clients, users, roles, Lambda functions, and runtimes.

The core conclusion is simple: AgentCore Runtime can be protected with AWS-native IAM, Cognito-issued JWTs, external IdP JWTs, and private networking. Those controls are complementary, but they are not interchangeable. IAM proves AWS principal authorization, JWT authorizers prove token issuer and claim trust, and VPC endpoints constrain network origin.
