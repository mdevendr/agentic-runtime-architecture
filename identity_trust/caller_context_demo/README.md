# Caller Context Demo

This demo evidences the article's identity-substitution hardening pattern locally and through a deployable AgentCore Runtime + Gateway path.

It models a Gateway-fronted Runtime substitution mode where Runtime authorizes a Gateway-controlled identity, but the agent application still verifies who the request is being performed for.

```text
Client identity
  -> trusted mediation layer signs caller-context assertion
  -> Gateway invokes Runtime with substituted identity
  -> Runtime application verifies caller-context assertion
  -> who_am_i tool returns original caller context
```

## What This Proves

- Runtime-facing identity can be different from original caller identity.
- A signed caller-context assertion can preserve original caller attribution.
- The Runtime application can verify the assertion before using it for audit, tenant policy, or business authorization.

## What This Does Not Claim

This demo does not claim that AgentCore Gateway natively signs or injects caller-context assertions. If a managed Gateway does not provide that hook, the signer can be a trusted Gateway-adjacent policy service or identity component.

AgentCore Gateway also supports REQUEST interceptor Lambdas and target header propagation. The deployed header path in this folder uses that mechanism: the Gateway invokes an interceptor Lambda, the interceptor signs caller context, Gateway forwards the assertion as `X-Amzn-Bedrock-AgentCore-Runtime-Custom-Caller-Context-Assertion`, and Runtime reads the header through `RequestContext`.

## Local Smoke Test

```bash
python identity_trust/caller_context_demo/invoke_with_caller_context.py
```

Expected output:

```json
{
  "runtime_authorized_identity": "GatewayServiceRole",
  "caller_context_verified": true,
  "original_caller": "user-123",
  "tenant": "tenant-a"
}
```

Negative test:

```bash
python identity_trust/caller_context_demo/tamper_test.py
```

Expected output:

```text
caller context rejected: caller context audience mismatch
```

## Runtime Boundary Evidence

This is the main evidence for the article. Runtime has already accepted the Runtime-facing identity, such as `GatewayServiceRole`, and the agent application must decide whether the caller-context assertion is valid.

```bash
python identity_trust/caller_context_demo/runtime_boundary_evidence.py
```

Good evidence:

- Runtime verifies the caller-context assertion signature.
- Runtime verifies the audience is the expected Runtime.
- Runtime returns `caller_context_verified: true`.
- Runtime recovers the original caller, tenant, session, and correlation ID.

Bad evidence:

- Wrong audience is rejected at the Runtime application boundary.
- Tampered signature is rejected at the Runtime application boundary.
- The decision is made by Runtime-side verification, not by Gateway routing.

## Gateway Interceptor Plumbing Evidence

This is supporting plumbing evidence only. It proves the Gateway REQUEST interceptor can mint and attach the assertion that Runtime later verifies.

```bash
python identity_trust/caller_context_demo/interceptor_evidence.py
```

Good evidence:

- interceptor returns `transformedGatewayRequest`
- response contains `X-Amzn-Bedrock-AgentCore-Runtime-Custom-Caller-Context-Assertion`
- signed assertion verifies with expected subject and tenant

Bad evidence:

- interceptor returns `transformedGatewayResponse`
- status code is `400`
- error is `caller_context_rejected`
- request stops at the Gateway interceptor boundary and does not proceed to Runtime

## Deployed Runtime + Gateway Test: Client-Signed Payload

This path deploys a minimal Runtime app with a `who_am_i` tool, fronts it with Gateway using `GATEWAY_IAM_ROLE`, and invokes it with a client-signed caller-context assertion that simulates a trusted mediation layer.

```text
Client
  -> signs caller-context assertion for demo
  -> AgentCore Gateway target
  -> Runtime authorized as GatewayServiceRole
  -> Runtime app verifies caller-context assertion
  -> who_am_i returns original caller
```

Deploy Runtime:

```bash
cd identity_trust/caller_context_demo
python deploy_runtime.py
```

Create Gateway-fronted Runtime target using substituted identity:

```bash
python setup_gateway.py
```

Invoke through Gateway:

```bash
python invoke_gateway_client.py
```

Expected response includes:

```json
{
  "runtime_authorized_identity": "GatewayServiceRole",
  "caller_context_verified": true,
  "caller_context_source": "payload",
  "original_caller": "user-123",
  "tenant": "tenant-a"
}
```

## Deployed Runtime + Gateway Test: Gateway-Signed Header

This path keeps the same Runtime but moves signing to a Gateway REQUEST interceptor Lambda. The client no longer sends the caller-context assertion. The interceptor derives caller context from Gateway request context, with `x-demo-*` headers used only as test inputs for this evidence script.

```text
Client
  -> AgentCore Gateway target
  -> Gateway REQUEST interceptor signs caller-context assertion
  -> Gateway forwards X-Amzn-Bedrock-AgentCore-Runtime-Custom-Caller-Context-Assertion to Runtime target
  -> Runtime authorized as GatewayServiceRole
  -> Runtime app verifies header assertion
  -> who_am_i returns original caller
```

Deploy Runtime with a request header allowlist:

```bash
cd identity_trust/caller_context_demo
python deploy_runtime.py
```

Create Gateway, REQUEST interceptor Lambda, and Runtime target with target-level header propagation:

```bash
python setup_gateway_with_interceptor.py
```

Invoke through Gateway without a client-signed assertion:

```bash
python invoke_gateway_header_client.py
```

This client uses the explicit Gateway target URL, `/{targetName}/invocations`, and SigV4-signs the HTTP request as `bedrock-agentcore`. That keeps the evidence path aligned with the Gateway target boundary instead of relying on the `InvokeAgentRuntime` SDK operation against a rewritten endpoint URL.

Expected response includes:

```json
{
  "runtime_authorized_identity": "GatewayServiceRole",
  "caller_context_verified": true,
  "caller_context_source": "gateway_header",
  "original_caller": "user-123",
  "tenant": "tenant-a"
}
```

## Important Boundary Note

The client-signed payload path proves that Runtime can validate an expected caller issuer. The Gateway-signed header path proves that a trusted mediation boundary can preserve original caller context while Runtime still authorizes the substituted Gateway-facing identity.

In production, derive caller claims from Gateway-authenticated identity or authorizer context. Do not trust arbitrary client-provided caller attributes unless the Runtime explicitly trusts that client issuer directly.
