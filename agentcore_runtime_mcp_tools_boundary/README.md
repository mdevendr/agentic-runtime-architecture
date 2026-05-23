# AgentCore Runtime MCP Tools Boundary

This pattern hosts the agent inside Amazon Bedrock AgentCore Runtime while preserving the MCP execution boundary. The Runtime endpoint becomes the managed invocation surface, but tool execution still crosses MCP client sessions and stdio transport before reaching the owning MCP server.

Direct Tools are **retained only as a comparison baseline** in `../agentcore_runtime_direct_tools_baseline`. They are not used by this implementation.

New/changed files in this Prompt 2 implementation:

- `main.py` - AgentCore Runtime entrypoint, two MCP client sessions, `tools/list`, ownership routing, `tools/call`, Bedrock Converse loop.
- `mcp_order_server.py` - MCP stdio server that owns `calculate_order_total`.
- `mcp_refund_server.py` - MCP stdio server that owns `check_refund_eligibility`.
- `mcp_smoke_test.py` - local MCP-only test for two server startups, `tools/list`, success `tools/call`, and failure `tools/call`.
- `deploy_runtime.py` - Prompt 1 deployment scaffold adapted to package the MCP runtime and server file.
- `invoke_runtime.py` - Prompt 1 invocation scaffold adapted to validate MCP evidence.
- `requirements.txt` - runtime dependencies including `mcp`.

## Implementation

The runtime deploys one agent application and two MCP server files in the same AgentCore artifact. At invocation time the agent starts each MCP server as a child process over stdio.

Assumption marked for this step: the selected AgentCore Runtime environment permits the deployed Python process to start a Python child process from the packaged artifact for stdio MCP transport. The local smoke test proves the MCP boundary; the deployed runtime invocation proves that this assumption holds in the target AgentCore environment.

Difference from the locally hosted MCP implementation:

```text
Local:
User -> local agent process -> MCP clients -> stdio -> MCP servers

AgentCore hosted:
User/client -> AgentCore Runtime endpoint -> deployed agent process -> MCP clients -> stdio -> MCP servers
```

The MCP boundary is the same in both. This implementation adds the AgentCore runtime endpoint, runtime ARN/session invocation, READY lifecycle state, managed execution role, and deployment artifact.

```text
AgentCore Runtime
  -> agent application in main.py
      -> MCPClient for ORDER
      -> stdio transport
      -> mcp_order_server.py
          -> calculate_order_total

      -> MCPClient for REFUND
      -> stdio transport
      -> mcp_refund_server.py
          -> check_refund_eligibility
```

The agent does not import or call the tool functions. It discovers each server's contracts through MCP `tools/list`, builds a `tool_name -> owning MCP session` route map, and invokes tools through MCP `tools/call`.

## Infrastructure Setup

Only the model id is required from the operator. You can provide it as an environment variable or let `deploy_runtime.py` prompt for it:

```bash
export BEDROCK_MODEL_ID="<bedrock-model-id-with-tool-use-support>"
```

Optional overrides:

```bash
export AWS_REGION="eu-west-2"
export AGENTCORE_RUNTIME_NAME="mcp_tools_boundary"
```

`deploy_runtime.py` creates:

- S3 code bucket: `agentcore-mcp-boundary-<account-id>-<region>`
- IAM execution role: `AmazonBedrockAgentCoreMcpToolsBoundary-<region>`
- Inline execution policy: `AgentCoreMcpToolsBoundaryPolicy`

The deployer identity must have permission to create and manage these generated resources, including `sts:GetCallerIdentity`, `s3:CreateBucket`, `s3:ListBucket`, `s3:PutObject`, `iam:CreateRole`, `iam:GetRole`, `iam:UpdateAssumeRolePolicy`, `iam:PutRolePolicy`, `iam:PassRole`, `bedrock-agentcore-control:CreateAgentRuntime`, and `bedrock-agentcore-control:GetAgentRuntime`.

Install local deployment dependencies:

```bash
pip install boto3 uv
```

Deploy:

```bash
python deploy_runtime.py
```

Expected deploy proof:

```text
Creating code bucket: s3://agentcore-mcp-boundary-<account-id>-<region>
Creating execution role: AmazonBedrockAgentCoreMcpToolsBoundary-<region>
Uploading build/agentcore_mcp_tools_boundary.zip to s3://agentcore-mcp-boundary-<account-id>-<region>/mcp_tools_boundary/agentcore_mcp_tools_boundary.zip
AgentCore Runtime create response:
...
Runtime status: READY
```

Set the runtime ARN from the deploy output:

```bash
export AGENT_RUNTIME_ARN="<agent-runtime-arn-from-deploy-output>"
```

## Runtime Invocation

Python success invoke:

```bash
python invoke_runtime.py --mode success
```

Python failure invoke:

```bash
python invoke_runtime.py --mode failure
```

`invoke_runtime.py` validates that the response includes:

- successful `tools/list`
- a `tools/call` event
- `boundary: "mcp"` in the tool result
- expected success or error status

`tool_choice` is used only on the first Bedrock Converse call to force proof of MCP invocation. After the MCP `tools/call` result is returned, follow-up Converse calls keep the tool schemas but remove forced `toolChoice` so the model can produce the final answer instead of repeatedly calling the same tool.

Expected verification:

```text
Verified MCP tools/list and tools/call over stdio: calculate_order_total returned status=success
```

Local one-shot invoke:

```bash
export AWS_REGION="eu-west-2"
export BEDROCK_MODEL_ID="<bedrock-model-id-with-tool-use-support>"
python main.py \
  --tool-choice calculate_order_total \
  "Call calculate_order_total with exactly these arguments: sku is SKU-BOOK-001, quantity is 3, and unit_price is 12.50."
```

Local server mode:

```bash
python main.py
```

Plain `python main.py` is a long-running local AgentCore `/invocations` server. Stop it with `Ctrl+C`.

## Tests

### Local MCP Smoke Test

Run this before deployment to prove the MCP server and stdio boundary work without AWS:

```bash
pip install mcp
python mcp_smoke_test.py
```

Expected evidence:

```text
MCP smoke test passed: two servers, tools/list, and tools/call
```

### Runtime Status

Run:

```bash
python deploy_runtime.py
```

Required evidence:

```text
Runtime status: READY
```

### MCP Server Starts

Required runtime log evidence:

```text
[RUNTIME -> AGENT] Starting ORDER MCP server over stdio
[RUNTIME -> AGENT] Starting REFUND MCP server over stdio
[MCP ORDER SERVER] Starting stdio server
[MCP REFUND SERVER] Starting stdio server
```

### tools/list Succeeds

Required response evidence:

```json
{
  "mcp_events": [
    {
      "event": "tools/list",
      "status": "success",
      "transport": "stdio",
      "tools": [
        {
          "name": "calculate_order_total",
          "mcp_server": "ORDER"
        },
        {
          "name": "check_refund_eligibility",
          "mcp_server": "REFUND"
        }
      ]
    }
  ]
}
```

Required runtime log evidence:

```text
[AGENT -> ORDER MCPCLIENT -> STDIO -> ORDER MCP SERVER] tools/list
[ORDER MCP SERVER -> STDIO -> ORDER MCPCLIENT -> AGENT] tools/list returned 1 tools
[AGENT -> REFUND MCPCLIENT -> STDIO -> REFUND MCP SERVER] tools/list
[REFUND MCP SERVER -> STDIO -> REFUND MCPCLIENT -> AGENT] tools/list returned 1 tools
```

### tools/call Succeeds

Run:

```bash
python invoke_runtime.py --mode success
```

Required response evidence:

```json
{
  "mcp_events": [
    {
      "event": "tools/call",
      "tool_name": "calculate_order_total",
      "status": "success",
      "transport": "stdio",
      "result": {
        "boundary": "mcp",
        "mcp_server": "ORDER",
        "mcp_tool": "calculate_order_total",
        "mcp_is_error": false
      }
    }
  ]
}
```

Required runtime log evidence:

```text
[LLM -> AGENT] Tool selected: calculate_order_total
[AGENT -> ORDER MCPCLIENT -> STDIO -> ORDER MCP SERVER] tools/call params=...
[MCP ORDER SERVER] Executing calculate_order_total
[ORDER MCP SERVER -> STDIO -> ORDER MCPCLIENT -> AGENT] tools/call status=success
```

### Failure Case

Run:

```bash
python invoke_runtime.py --mode failure
```

The failure case passes `BOOK-001` as the SKU. Validation happens inside `mcp_order_server.py`, not inside the agent.

Required evidence:

```json
{
  "event": "tools/call",
  "tool_name": "calculate_order_total",
  "status": "error",
  "result": {
    "boundary": "mcp",
    "mcp_tool": "calculate_order_total",
    "mcp_is_error": true
  }
}
```

## Architecture

### A. What Changes From Prompt 1

Prompt 1 proved:

```text
Runtime -> Agent -> Direct Tool
```

Prompt 2 proves:

```text
Runtime -> Agent -> MCPClient -> stdio transport -> owning MCP Server -> Tool
```

Prompt 1 tool schemas, validation, and handlers lived in the agent process. Prompt 2 moves tool schema publication, validation, and execution into the owning MCP server.

### B. Agent vs MCP Server Separation

The agent owns reasoning and orchestration:

- receives the AgentCore runtime request
- calls Bedrock Converse
- exposes MCP-discovered schemas to the LLM
- maps selected tool names to MCP adapters
- routes tool calls through MCPClient

Each MCP server owns tool execution for its domain:

- publishes tool schemas through `tools/list`
- validates tool input
- executes tool code
- returns tool results or tool errors

The agent receives callable adapters/proxies through MCPClient. It does not own or bypass the implementation code.

### C. MCPClient Behaviour Inside Runtime

Inside the AgentCore Runtime process, `main.py`:

- starts `mcp_order_server.py` and `mcp_refund_server.py` as stdio child processes
- creates one MCP `ClientSession` per server
- calls `tools/list` on each server
- converts MCP tool schemas to Bedrock Converse tool specs
- builds a `tool_name -> owning MCP session` route map
- routes LLM-selected tools through the owning MCP session using `tools/call`
- returns MCP results back to the LLM as Bedrock `toolResult`

This preserves the MCP boundary because tool invocation crosses MCPClient and stdio transport before execution.

### D. Tool Contract Location

The tool contract lives in the MCP server:

- tool names: `@mcp.tool()` functions in `mcp_order_server.py` and `mcp_refund_server.py`
- descriptions: function docstrings in each MCP server
- input schemas: generated and exposed by the MCP server through `tools/list`
- validation: explicit checks inside MCP tool functions
- execution: MCP server tool functions

The LLM sees schema only. Execution authority remains inside the MCP server.

### E. Trust Boundary

This baseline uses a **stdio process boundary**.

Compared with Direct Tools, this adds:

- separate child process for tool execution
- JSON-RPC/MCP protocol boundary
- schema discovery through `tools/list`
- invocation through `tools/call`
- agent/tool code separation

It does not yet add:

- network isolation
- separate IAM identity
- AgentCore Gateway
- platform-managed MCP authorization

### F. Execution Flow

```text
User
-> AgentCore Runtime
-> Agent
-> LLM
-> tool selection
-> Agent framework
-> MCPClient for the selected tool owner
-> stdio transport
-> owning MCP Server
-> Tool
-> Result
-> MCPClient
-> Agent
-> LLM
-> Response
```

### G. Failure Points

Failure points:

- MCP server file unavailable
- MCP server startup failure
- stdio transport failure
- `tools/list` failure
- LLM selects unknown MCP tool
- schema validation failure inside the MCP server
- MCP tool runtime exception
- Bedrock model invocation failure
- AgentCore runtime deployment or invocation failure

## Proof

Proven when the validation steps above are executed:

- AgentCore Runtime invocation succeeds.
- `tools/list` succeeds from the agent runtime path.
- `tools/call` succeeds from the agent runtime path.
- Logs prove `Runtime -> Agent -> MCPClient -> stdio transport -> owning MCP Server -> Tool`.
- Tool execution is not local to the agent; it is executed by `mcp_order_server.py` or `mcp_refund_server.py`.
- Agent does not bypass MCP because `main.py` has no local tool handlers.

Not yet proven:

- AgentCore Gateway or platform-managed MCP boundary.
- Identity enforcement between agent and tool server.
- Production observability and failure handling.
- Network-isolated remote MCP execution.

## Validation Checklist

- [ ] Prompt 1 direct-tools runtime remains available as comparison baseline.
- [ ] `python mcp_smoke_test.py` passes locally.
- [ ] `python deploy_runtime.py` completes.
- [ ] Runtime status is `READY`.
- [ ] Runtime logs show MCP server startup.
- [ ] Runtime logs show `tools/list`.
- [ ] Runtime response includes `mcp_events` with successful `tools/list`.
- [ ] `python invoke_runtime.py --mode success` verifies MCP `tools/call`.
- [ ] Runtime logs show `[MCP ORDER SERVER] Executing calculate_order_total`.
- [ ] `python invoke_runtime.py --mode failure` returns MCP validation error evidence.
- [ ] Response evidence includes `boundary: "mcp"`.
- [ ] No direct local tool handlers are used by the agent.
