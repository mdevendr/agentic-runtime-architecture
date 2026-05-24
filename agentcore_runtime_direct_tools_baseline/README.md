# AgentCore Runtime Direct Tools Baseline

This pattern proves the Stage 1 hosted baseline: Amazon Bedrock AgentCore Runtime is the managed invocation entry point, while tool execution remains direct and in-process.

This is the same Direct Tooling execution model as `direct-tools-architecture`; the difference is hosting. In this baseline, the agent application and direct tools are packaged into an AgentCore Runtime artifact and invoked through AgentCore Runtime instead of running as a standalone local Python script.

The tool boundary does not change:

```text
Agent application -> local tool dispatcher -> in-process Python tool
```

The runtime boundary does change:

```text
Client -> AgentCore Runtime endpoint -> deployed agent application
```

Sources used for implementation:

- AWS AgentCore direct code deployment requires an entrypoint file using `BedrockAgentCoreApp` or `/invocations` and `/ping` endpoints, and supports deploying Python code as a ZIP package to AgentCore Runtime: [AWS docs](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-get-started-code-deploy-python.html).
- AgentCore Runtime is invoked through `InvokeAgentRuntime` with an Agent Runtime ARN, runtime session ID, and binary payload: [AWS docs](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-invoke-agent.html).

## Implementation

Files:

- `main.py` - AgentCore Runtime entrypoint, one agent, two direct in-process tools, Bedrock Converse tool loop.
- `requirements.txt` - runtime dependencies.
- `deploy_runtime.py` - packages code, uploads to S3, creates AgentCore Runtime, waits for `READY`.
- `invoke_runtime.py` - invokes the deployed runtime with success or failure payloads.

Direct tools:

- `calculate_order_total`
- `check_refund_eligibility`

Both tools define Pydantic schemas and validation in `main.py`. Unknown fields are rejected, required fields are enforced, numeric ranges are checked, and custom SKU/order-id validators enforce domain rules. Tool handlers are local Python functions executed inside the AgentCore Runtime process.

## Infrastructure Setup

Only the model id is required from the operator. You can provide it as an environment variable or let `deploy_runtime.py` prompt for it:

```bash
export BEDROCK_MODEL_ID="<bedrock-model-id-with-tool-use-support>"
```

Optional overrides:

```bash
export AWS_REGION="us-west-2"
export AGENTCORE_RUNTIME_NAME="direct_tools_baseline"
```

`deploy_runtime.py` creates the AgentCore code bucket and execution role automatically. It derives the AWS account id with STS and creates:

- S3 code bucket: `agentcore-dt-baseline-<account-id>-<region>`
- IAM execution role: `AmazonBedrockAgentCoreDirectToolsBaseline-<region>`
- Inline execution policy: `AgentCoreDirectToolsBaselinePolicy`

The generated execution role allows AgentCore Runtime to read the deployment package from S3, write runtime logs and metrics, emit traces, and invoke the selected Bedrock model.

Generated runtime execution role trust policy:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "AssumeRolePolicy",
      "Effect": "Allow",
      "Principal": {
        "Service": "bedrock-agentcore.amazonaws.com"
      },
      "Action": "sts:AssumeRole",
      "Condition": {
        "StringEquals": {
          "aws:SourceAccount": "<account-id>"
        },
        "ArnLike": {
          "aws:SourceArn": "arn:aws:bedrock-agentcore:<region>:<account-id>:*"
        }
      }
    }
  ]
}
```

The deployer identity must have permission to create and manage these generated resources, including `sts:GetCallerIdentity`, `s3:CreateBucket`, `s3:ListBucket`, `s3:PutObject`, `iam:CreateRole`, `iam:GetRole`, `iam:UpdateAssumeRolePolicy`, `iam:PutRolePolicy`, `iam:PassRole`, `bedrock-agentcore-control:CreateAgentRuntime`, and `bedrock-agentcore-control:GetAgentRuntime`.

Install local deployment dependencies:

```bash
pip install boto3
pip install uv
```

Deploy:

```bash
python deploy_runtime.py
```

The deployment script builds a ZIP package using Linux arm64 wheels:

```text
uv pip install --python-platform aarch64-manylinux2014 --python-version 3.13 --target build/package --only-binary=:all: -r requirements.txt
```

Expected deploy proof:

```text
Creating code bucket: s3://agentcore-dt-baseline-<account-id>-<region>
Creating execution role: AmazonBedrockAgentCoreDirectToolsBaseline-<region>
Uploading build/agentcore_direct_tools_baseline.zip to s3://agentcore-dt-baseline-<account-id>-<region>/direct_tools_baseline/agentcore_direct_tools_baseline.zip
AgentCore Runtime create response:
...
Runtime status: CREATING
Runtime status: READY
Runtime is READY:
...
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

You do not need to run `python main.py` before invoking the deployed runtime. `deploy_runtime.py` packages `main.py` into the AgentCore runtime artifact, and AgentCore runs that deployed entrypoint. Local `python main.py` is only for local development.

The invocation is only considered proven if the response contains a non-empty `tool_events` array. `invoke_runtime.py` validates this and exits with an error if the runtime response does not prove direct tool execution.

AWS CLI invoke:

```bash
aws bedrock-agentcore invoke-agent-runtime \
  --region "$AWS_REGION" \
  --agent-runtime-arn "$AGENT_RUNTIME_ARN" \
  --runtime-session-id "session-00000000-0000-4000-8000-000000000001" \
  --content-type "application/json" \
  --accept "application/json" \
  --payload '{"prompt":"Calculate the total for SKU-BOOK-001. Quantity is 3 and unit price is 12.50. Use the available tool.","tool_choice":"calculate_order_total"}' \
  response.json
```

`curl` against the hosted AgentCore Runtime is only practical when you sign the request with AWS SigV4 or use an OAuth-authenticated HTTPS flow. For this minimal baseline, the supported hosted examples are Python/boto3 and AWS CLI. Local AgentCore dev curl is supported when running the runtime locally with AgentCore tooling:

```bash
curl -X POST http://localhost:8080/invocations \
  -H "Content-Type: application/json" \
  -d '{"prompt":"Calculate the total for SKU-BOOK-001. Quantity is 3 and unit price is 12.50. Use the available tool.","tool_choice":"calculate_order_total"}'
```

Note: externally the invocation surface is unchanged. Callers still supply `tool_choice` or let the model select a tool. Internally the runtime routes the selected tool through an in-process dispatcher wrapper before the local Python handler runs. The `[GATEWAY:*]` log prefix in this sample is a local dispatcher label only; it is not AgentCore Gateway.

## Tests

### Run `main.py` Locally

`main.py` is the AgentCore Runtime application entrypoint. It supports two local execution modes.

Run one prompt and exit:

```bash
export AWS_REGION="us-west-2"
export BEDROCK_MODEL_ID="<bedrock-model-id-with-tool-use-support>"
python main.py \
  --tool-choice calculate_order_total \
  "Calculate the total for SKU-BOOK-001. Quantity is 3 and unit price is 12.50. Use the available tool."
```

The one-shot command calls Bedrock, so it requires working AWS credentials, access to the selected model, and network access to Bedrock Runtime. Local Bedrock calls are bounded by `BEDROCK_CONNECT_TIMEOUT_SECONDS=10` and `BEDROCK_READ_TIMEOUT_SECONDS=60` by default.

Expected output shape:

```json
{
  "result": "<LLM final response>",
  "tool_events": [
    {
      "tool_name": "calculate_order_total",
      "status": "success"
    }
  ]
}
```

Start the local AgentCore app server. This is a long-running process and does not return until you stop it:

```bash
export AWS_REGION="us-west-2"
export BEDROCK_MODEL_ID="<bedrock-model-id-with-tool-use-support>"
python main.py
```

Expected terminal message:

```text
Starting AgentCore local server on /invocations. This command keeps running until Ctrl+C.
```

In another Git Bash terminal, invoke the local runtime endpoint:

```bash
curl -X POST http://localhost:8080/invocations \
  -H "Content-Type: application/json" \
  -d '{"prompt":"Calculate the total for SKU-BOOK-001. Quantity is 3 and unit price is 12.50. Use the available tool.","tool_choice":"calculate_order_total"}'
```

Expected local server logs:

```text
[RUNTIME] Sending prompt to LLM with direct tool schemas
[AGENT] Tool selected by LLM: calculate_order_total
[AGENT] Tool input validation passed
[DIRECT TOOL] Executing calculate_order_total in-process
[RUNTIME] Returning direct tool result to LLM
```

Plain `python main.py` is not the one-shot test path. It keeps running because it is serving `/invocations`. Stop the local server with `Ctrl+C`.

### Runtime Status

Run:

```bash
python deploy_runtime.py
```

Required evidence:

```text
Runtime status: READY
```

### Successful Runtime Invocation

Run:

```bash
python invoke_runtime.py --mode success
```

Expected tool execution logs in AgentCore Runtime logs:

```text
[RUNTIME] Sending prompt to LLM with direct tool schemas
[AGENT] Tool selected by LLM: calculate_order_total
[AGENT] Raw tool input: {"sku": "SKU-BOOK-001", "quantity": 3, "unit_price": 12.5}
[AGENT] Tool input validation passed
[GATEWAY:calculate_order_total] Received request: {"sku": "SKU-BOOK-001", "quantity": 3, "unit_price": 12.5}
[DIRECT TOOL] Executing calculate_order_total in-process
[AGENT] Tool execution result: {"sku": "SKU-BOOK-001", ... "total": 45.0}
[RUNTIME] Returning direct tool result to LLM
```

Required response evidence:

```json
{
  "result": "<LLM final response containing the order total>",
  "tool_events": [
    {
      "tool_name": "calculate_order_total",
      "status": "success",
      "result": {
        "total": 45.0
      }
    }
  ]
}
```

`invoke_runtime.py` also prints verification evidence to stderr:

```text
Verified direct tool execution: calculate_order_total returned status=success
```

### Failure Case

Run:

```bash
python invoke_runtime.py --mode failure
```

Expected validation logs:

```text
[AGENT] Tool selected by LLM: calculate_order_total
[AGENT] Raw tool input: {"sku": "BOOK-001", "quantity": 3, "unit_price": 12.5}
[AGENT] Tool input validation failed: Value error, sku must start with SKU-
```

The failure invocation forces the LLM to select `calculate_order_total` with `toolChoice` and asks it to pass the invalid SKU exactly as supplied. The rejection is performed by the local Pydantic schema before the Python handler runs.

Required response evidence:

```json
{
  "result": "<LLM final response explaining the validation error>",
  "tool_events": [
    {
      "tool_name": "calculate_order_total",
      "status": "error",
      "result": {
        "error": "ValidationError",
        "details": [
          {
            "field": "sku",
            "message": "Value error, sku must start with SKU-"
          }
        ]
      }
    }
  ]
}
```

`invoke_runtime.py` also prints verification evidence to stderr:

```text
Verified direct tool execution: calculate_order_total returned status=error
```

## Architecture

### A. Runtime Entry Point

AgentCore Runtime is the external entry point. Clients invoke the runtime through the Agent Runtime ARN using `InvokeAgentRuntime`, passing a runtime session ID and a JSON payload.

The runtime identifies this agent through the deployed runtime artifact and configured entrypoint:

```python
app = BedrockAgentCoreApp()

@app.entrypoint
def agentcore_entrypoint(request):
    ...
```

The runtime endpoint is the hosted invocation surface. The agent-specific identity is the Agent Runtime ARN and runtime version/qualifier used at invocation time.

### B. Direct Tool Contract

Tool contract location:

- tool name: `TOOLS` registry in `main.py`
- tool description: `TOOLS` registry in `main.py`
- tool input schema: Pydantic models in `main.py`
- validation: Pydantic field constraints, `extra="forbid"`, and custom validators in `main.py`
- execution: local Python handler functions in `main.py`

Invalid input is handled by `execute_tool`. Pydantic raises `ValidationError`, the agent logs the validation failure, sanitizes the error into JSON-safe fields, and returns a Bedrock `toolResult` with `status: "error"`.

### C. Execution Flow

```text
User
-> AgentCore Runtime
-> agentcore_entrypoint
-> LLM via Bedrock Converse
-> tool selection
-> local Pydantic validation
-> local Python tool execution
-> tool result
-> LLM
-> final response
```

The LLM does not execute tools. It selects a tool and supplies JSON arguments. The Python agent framework executes the tool locally in the AgentCore Runtime process.

### D. Trust Boundary

This baseline has no protocol boundary around tools.

Tool execution shares:

- same Python process
- same memory boundary
- same runtime permissions
- same deployment artifact
- same failure blast radius

AgentCore Runtime is the external entry point and managed hosting boundary, but the tools themselves remain direct in-process functions.

### E. Failure Points

Failure points:

- invalid arguments from LLM
- Pydantic validation failure
- local tool runtime exception
- LLM selects a nonexistent tool
- Bedrock model invocation failure
- AgentCore Runtime deployment or invocation failure

Failures are surfaced as either runtime errors, structured tool errors, or final LLM responses that include the tool error.

## Proof

Proven when the validation steps above are executed:

- AgentCore Runtime invocation works.
- Runtime status reaches `READY`.
- LLM emits a tool selection.
- The direct tool executes in-process.
- Tool result returns to the agent and then to the LLM.
- Invalid tool input is rejected locally by Pydantic validation.

Not proven in this Stage 1 baseline:

- MCP boundary.
- Remote tool execution.
- Gateway tool execution.
- Identity or security separation between agent and tools.

## Validation Checklist

- [ ] `python main.py --tool-choice calculate_order_total "<prompt>"` runs one local prompt and exits.
- [ ] `python main.py` starts the local AgentCore app server.
- [ ] `python deploy_runtime.py` completes.
- [ ] Runtime status is `READY`.
- [ ] `python invoke_runtime.py --mode success` returns a successful agent response.
- [ ] AgentCore logs show `Tool selected by LLM`.
- [ ] AgentCore logs show `[DIRECT TOOL] Executing calculate_order_total in-process`.
- [ ] AgentCore logs show tool result returned to the LLM.
- [ ] `python invoke_runtime.py --mode failure` returns a structured validation error.
- [ ] AgentCore logs show local validation failure.
- [ ] No MCP server, gateway, or remote tool boundary is used.
