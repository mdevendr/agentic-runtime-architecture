# AgentCore Gateway Tools Boundary

This pattern introduces Amazon Bedrock AgentCore Gateway as the platform-mediated MCP tool boundary. Runtime remains the reasoning and orchestration boundary, Gateway owns tool exposure and routing, and downstream Lambda targets own execution.

Gateway is the policy enforcement point for tool exposure in this pattern. It owns the visible tool surface, target binding, credential mode, and invocation path between the agent runtime and downstream capabilities.

Local MCP servers from Stage 2 are **retained only for comparison** in `../agentcore_runtime_mcp_tools_boundary`. They are not used by this implementation.

Core files in this Stage 3 Gateway-mediated implementation:

- `setup_gateway.py` - creates IAM roles, two Lambda targets (one per tool), AgentCore Gateway, and Gateway target attachments.
- `lambda_calculate_order_total.py` - Lambda target that owns `calculate_order_total` execution.
- `lambda_check_refund_eligibility.py` - Lambda target that owns `check_refund_eligibility` execution.
- `tool_schema.json` - Gateway tool schemas (both tools).
- `main.py` - AgentCore Runtime entrypoint, MCP streamable-HTTP client to AgentCore Gateway, Bedrock Converse loop.
- `deploy_runtime.py` - deploys the AgentCore Runtime agent with `AGENTCORE_GATEWAY_URL`.
- `invoke_runtime.py` - validates Gateway `tools/list`, Gateway `tools/call`, and boundary evidence.
- `requirements.txt` - runtime dependencies.

Assumption for this pattern: the Gateway is created with `AWS_IAM` inbound authorization by default. The agent signs Gateway MCP requests with SigV4 using the AgentCore Runtime execution role. For development only, `AGENTCORE_GATEWAY_AUTHORIZER=NONE` can be used if the Gateway is created that way.

## Implementation

Stage 2 proved:

```text
Runtime -> Agent -> MCPClient -> stdio transport -> MCP Server -> Tool
```

Stage 3 proves two per-tool Gateway Lambda targets:

```text
Runtime -> Agent -> MCPClient -> AgentCore Gateway -> Lambda #1 (calculate_order_total)
                                                   -> Lambda #2 (check_refund_eligibility)
```

The agent does not start local MCP servers. AgentCore Gateway exposes a single MCP endpoint that aggregates two per-tool Lambda targets. Each Lambda function owns execution for one tool. The Gateway routes tool requests to the appropriate Lambda target.

For production hardening, pair Gateway mediation with:

- static schema catalogs from `../schema_catalog/`
- correlation context from `../shared/execution_context.py`
- idempotency keys from `../shared/idempotency.py` for mutating targets
- circuit breakers from `../shared/circuit_breaker.py` to bound recursive tool loops

## Infrastructure Setup

Required model id:

```bash
export BEDROCK_MODEL_ID="<bedrock-model-id-with-tool-use-support>"
```

Optional overrides:

```bash
export AWS_REGION="eu-west-2"
export AGENTCORE_GATEWAY_NAME="gateway-tools-boundary"
export AGENTCORE_GATEWAY_LAMBDA_NAME_CALCULATE_ORDER_TOTAL="agentcore_gateway_calculate_order_total"
export AGENTCORE_GATEWAY_LAMBDA_NAME_CHECK_REFUND_ELIGIBILITY="agentcore_gateway_check_refund_eligibility"
export AGENTCORE_GATEWAY_AUTHORIZER="AWS_IAM"
```

Install local deployment dependencies:

```bash
pip install boto3 uv
```

Create Gateway, Lambda targets, IAM roles, and target attachments:

```bash
python setup_gateway.py
```

Expected proof:

```text
Gateway create response:
...
Gateway status: CREATING
Gateway status: READY
Gateway target create response for calculate_order_total:
...
Gateway target create response for check_refund_eligibility:
...
AGENTCORE_GATEWAY_ID=<gateway-id>
AGENTCORE_GATEWAY_ARN=<gateway-arn>
AGENTCORE_GATEWAY_URL=<gateway-url>
AGENTCORE_GATEWAY_LAMBDA_ARN_CALCULATE_ORDER_TOTAL=<lambda-arn-1>
AGENTCORE_GATEWAY_LAMBDA_ARN_CHECK_REFUND_ELIGIBILITY=<lambda-arn-2>
AGENTCORE_GATEWAY_TARGET_ID_CALCULATEORDERTOTAL=<target-id-1>
AGENTCORE_GATEWAY_TARGET_ID_CHECKREFUNDELIGIBILITY=<target-id-2>
```

Set the Gateway URL:

```bash
export AGENTCORE_GATEWAY_URL="<gateway-url-from-setup-output>"
export AGENTCORE_GATEWAY_ARN="<gateway-arn-from-setup-output>"
```

Deploy the agent runtime:

```bash
python deploy_runtime.py
```

Set the runtime ARN:

```bash
export AGENT_RUNTIME_ARN="<agent-runtime-arn-from-deploy-output>"
```

## IAM Setup

`setup_gateway.py` creates:

- Lambda execution roles: `AgentCoreGatewayCalculateOrderTotalLambdaRole-<region>` and `AgentCoreGatewayCheckRefundEligibilityLambdaRole-<region>`
- Gateway service role: `AgentCoreGatewayBoundaryRole-<region>`
- Lambda functions: `agentcore_gateway_calculate_order_total` and `agentcore_gateway_check_refund_eligibility`
- AgentCore Gateway: `gateway-tools-boundary`
- Gateway Lambda targets: `calculateordertotalTarget` and `checkrefundeligibilityTarget`

The Lambda execution roles allow CloudWatch Logs writes.

The Gateway service role allows:

```json
{
  "Action": ["lambda:InvokeFunction"],
  "Resource": ["<calculate-lambda-arn>", "<refund-lambda-arn>"]
}
```

Each Lambda function resource policy allows the Gateway service role to call `lambda:InvokeFunction`.

The agent runtime execution role created by `deploy_runtime.py` includes:

```json
{
  "Action": ["bedrock-agentcore:InvokeGateway"],
  "Resource": "<gateway-arn if AGENTCORE_GATEWAY_ARN is set, otherwise *>"
}
```

When `AGENTCORE_GATEWAY_AUTHORIZER=AWS_IAM`, `deploy_runtime.py` now requires `AGENTCORE_GATEWAY_ARN` and the runtime role is limited to only that Gateway ARN.

## Stage 4: Identity and Trust Boundary

This architecture now enforces a layered identity boundary across AgentCore Runtime, AgentCore Gateway, and Lambda targets:

- The external caller must hold IAM permission to invoke the AgentCore runtime via `bedrock-agentcore:InvokeAgentRuntime`.
- The runtime execution role is least-privileged and only granted `bedrock-agentcore:InvokeGateway` on the configured Gateway ARN.
- The Gateway service role is least-privileged and only granted `lambda:InvokeFunction` on the two tool Lambda ARNs.
- Each Lambda target is configured with a resource policy that allows invocation only from the AgentCore Gateway principal, scoped to the created Gateway ARN when supported.

### Unauthorized test path

For proof of the security boundary, run:

```bash
python invoke_runtime.py --mode unauthorized --runtime-arn "<unauthorized-runtime-arn>"
```

or set:

```bash
export AGENT_RUNTIME_ARN_UNAUTHORIZED="<unauthorized-runtime-arn>"
python invoke_runtime.py --mode unauthorized
```

A protected deployment should return an AWS client error and not execute the Gateway tool flow.

## Runtime Invocation

Success:

```bash
python invoke_runtime.py --mode success
```

Failure:

```bash
python invoke_runtime.py --mode failure
```

The failure case passes `BOOK-001` as the SKU. Validation happens in the Lambda target, after the call crosses AgentCore Gateway.

Expected verification:

```text
Verified Gateway tools/list and tools/call: calculate_order_total returned status=success
```

## Tests

### Gateway Exists

Required setup evidence:

```text
AGENTCORE_GATEWAY_ID=<gateway-id>
AGENTCORE_GATEWAY_URL=<gateway-url>
```

### Targets Are Attached

Required setup evidence:

```text
AGENTCORE_GATEWAY_TARGET_ID_CALCULATEORDERTOTAL=<target-id-1>
AGENTCORE_GATEWAY_TARGET_ID_CHECKREFUNDELIGIBILITY=<target-id-2>
AGENTCORE_GATEWAY_LAMBDA_ARN_CALCULATE_ORDER_TOTAL=<lambda-arn-1>
AGENTCORE_GATEWAY_LAMBDA_ARN_CHECK_REFUND_ELIGIBILITY=<lambda-arn-2>
```

### tools/list Works Through Gateway

Required runtime response evidence:

```json
{
  "gateway_events": [
    {
      "event": "tools/list",
      "status": "success",
      "gateway_url": "<gateway-url>",
      "tools": ["calculateordertotalTarget___calculate_order_total"]
    }
  ]
}
```

Note: AgentCore Gateway prefixes Lambda target tool names with the target name. The visible name can be `${target_name}___${tool_name}`.

### tools/call Works Through Gateway

Required runtime response evidence:

```json
{
  "event": "tools/call",
  "status": "success",
  "result": {
    "boundary": "agentcore_gateway",
    "gateway_tool": "calculateordertotalTarget___calculate_order_total",
    "gateway_is_error": false
  }
}
```

### Downstream Target Logs

Required Lambda CloudWatch log evidence:

```text
[LAMBDA TARGET] event={"sku":"SKU-BOOK-001","quantity":3,"unit_price":12.5}
```

### Runtime Logs

Required AgentCore Runtime log evidence:

```text
[AGENT -> MCPCLIENT -> AGENTCORE GATEWAY] tools/list
[AGENT -> MCPCLIENT -> AGENTCORE GATEWAY -> TARGET] tools/call
[TARGET -> AGENTCORE GATEWAY -> MCPCLIENT -> AGENT] tools/call status=success
```

## Architecture

### A. What Changes From Stage 2

Stage 2:

```text
Runtime -> Agent -> MCPClient -> MCP Server -> Tool
```

Stage 3:

```text
Runtime -> Agent -> MCPClient -> AgentCore Gateway -> Target service
```

The MCP-facing tool endpoint is now managed by AgentCore Gateway. The local MCP server process is removed from this implementation.

### B. Local MCP vs Platform-Mediated Boundary

Stage 2 local MCP:

- local MCP server owns tool exposure
- local MCP server owns routing for its own process
- local MCP server owns execution

Stage 3 Gateway:

- AgentCore Gateway owns MCP exposure and routing
- Lambda target owns execution
- agent sees one Gateway MCP endpoint

### C. Tool Contract Location

The Gateway target schema is defined in `tool_schema.json` and attached with `create_gateway_target`.

The target implementations live in `lambda_calculate_order_total.py` and `lambda_check_refund_eligibility.py`.

The agent and LLM see only the MCP tool schema exposed by AgentCore Gateway. Actual execution happens in Lambda.

### D. Execution Flow

```text
User
-> AgentCore Runtime
-> Agent
-> LLM
-> tool selection
-> Agent framework
-> MCPClient
-> AgentCore Gateway
-> Lambda target service
-> Result
-> Gateway
-> MCPClient
-> Agent
-> LLM
-> Response
```

### E. Ownership and Scaling

- Agent lifecycle: deployed as an AgentCore Runtime artifact.
- Gateway lifecycle: managed independently as AgentCore Gateway.
- Target service lifecycle: Lambda function deployed and updated independently.
- Scaling implication: Lambda target scales independently from the agent runtime; Gateway mediates tool exposure and routing.

### F. Failure Points

- Gateway unavailable.
- Gateway target misconfigured.
- IAM permission denied for `bedrock-agentcore:InvokeGateway`.
- Gateway service role cannot invoke Lambda.
- Schema/contract mismatch.
- Lambda validation failure.
- Lambda runtime exception.
- Bedrock model invocation failure.

## Proof

Proven when the validation steps above are executed:

- Gateway identifier and endpoint exist.
- Lambda target is attached.
- `tools/list` succeeds through Gateway.
- `tools/call` succeeds through Gateway.
- Lambda target logs prove downstream execution.
- Runtime logs prove `Runtime -> Agent -> MCPClient -> Gateway -> Target`.

Not yet proven:

- formal end-user identity propagation and authorization model.
- negative security controls.
- production observability and failure handling.
- policy engine enforcement.

## Validation Checklist

- [ ] Stage 2 MCP runtime remains available as comparison baseline.
- [ ] `python setup_gateway.py` creates Gateway.
- [ ] Setup output includes `AGENTCORE_GATEWAY_ID`.
- [ ] Setup output includes `AGENTCORE_GATEWAY_URL`.
- [ ] Setup output includes `AGENTCORE_GATEWAY_TARGET_ID`.
- [ ] Lambda target exists.
- [ ] `python deploy_runtime.py` completes.
- [ ] Runtime status is `READY`.
- [ ] `python invoke_runtime.py --mode success` verifies Gateway `tools/list` and `tools/call`.
- [ ] Lambda logs show target execution.
- [ ] `python invoke_runtime.py --mode failure` returns invalid input failure evidence.
- [ ] Response evidence includes `boundary: "agentcore_gateway"`.
