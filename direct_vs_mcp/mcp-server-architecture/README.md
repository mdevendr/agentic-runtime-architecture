# MCP Server Architecture

This implementation demonstrates an AI agent that accesses tools through the Model Context Protocol (MCP). The agent process does not import or execute tool functions directly. It starts an MCP server as a separate Python process and communicates with it over stdio transport.

The stdio transport is the trust and execution boundary in this example: the agent owns orchestration and LLM interaction, while the MCP server owns tool contracts, validation, and execution.

## Code

Files:

- `mcp_agent.py` - MCP client, Bedrock Converse agent loop, MCP tool discovery, MCP-to-Bedrock tool wrapping, and tool-call routing.
- `mcp_server.py` - FastMCP stdio server with two server-side tools:
  - `calculate_order_total`
  - `check_refund_eligibility`

Run:

```powershell
pip install mcp boto3
$env:AWS_REGION = "eu-west-2"
$env:BEDROCK_MODEL_ID = "<bedrock-model-id-that-supports-tool-use>"
python .\mcp_agent.py success
python .\mcp_agent.py failure
```

Assumption: the configured Bedrock model supports tool use through the Converse API. Without `BEDROCK_MODEL_ID` and AWS credentials, the agent can still start the MCP server and discover tools, but it cannot complete the LLM selection step.

The implementation uses stdio MCP transport:

```python
server_params = StdioServerParameters(
    command=sys.executable,
    args=[str(SERVER_FILE)],
)

async with stdio_client(server_params) as (read, write):
    async with ClientSession(read, write) as mcp_client:
        await mcp_client.initialize()
        tools_response = await mcp_client.list_tools()
```

The agent wraps MCP-discovered tool schemas for Bedrock. This exposes only tool metadata to the LLM:

```python
{
    "toolSpec": {
        "name": tool.name,
        "description": tool.description or f"MCP tool {tool.name}",
        "inputSchema": {"json": tool.inputSchema},
    }
}
```

The agent routes selected tool calls back over MCP:

```python
result = await mcp_client.call_tool(tool_name, tool_input)
status = "error" if result.isError else "success"
```

The MCP server owns the actual implementation:

```python
@mcp.tool()
def calculate_order_total(sku: str, quantity: int, unit_price: float) -> dict[str, Any]:
    if not sku.startswith("SKU-"):
        raise ValueError("sku must start with SKU-")
    if quantity < 1:
        raise ValueError("quantity must be greater than or equal to 1")
    if unit_price <= 0:
        raise ValueError("unit_price must be greater than 0")
    ...
```

## Expected Test Output

### Local Validation Performed

Syntax validation:

```powershell
python -m py_compile .\mcp_agent.py .\mcp_server.py
```

Result: passed.

Discovery validation without Bedrock credentials:

```powershell
python .\mcp_agent.py success
```

Observed output:

```text
[AGENT] Starting MCP server as separate process
[MCP SERVER] Starting stdio server
[AGENT → MCP] JSON-RPC request: tools/list
Processing request of type ListToolsRequest
[MCP → AGENT] tools/list returned 2 tools
[MCP TOOL DISCOVERED] name=calculate_order_total ... schema={...}
[MCP TOOL DISCOVERED] name=check_refund_eligibility ... schema={...}
RuntimeError: Missing BEDROCK_MODEL_ID
```

This proves MCP server startup and `tools/list` discovery over stdio. It does not prove real Bedrock tool selection because Bedrock credentials were not available in the local validation environment.

### Successful Agent Path

Prompt:

```text
Calculate the total for SKU-BOOK-001. Quantity is 3 and unit price is 12.50. Use the available MCP tool.
```

Expected logs with Bedrock configured:

```text
[AGENT] Starting MCP server as separate process
[AGENT → MCP] JSON-RPC request: tools/list
[MCP → AGENT] tools/list returned 2 tools
[MCP TOOL DISCOVERED] name=calculate_order_total ... schema={...}
[AGENT → LLM] Sending prompt with MCP-discovered tool schemas
[LLM → AGENT] Tool selected: calculate_order_total
[LLM → AGENT] Tool arguments: {"sku": "SKU-BOOK-001", "quantity": 3, "unit_price": 12.5}
[AGENT → MCP] JSON-RPC request: tools/call params={"name": "calculate_order_total", "arguments": {"sku": "SKU-BOOK-001", "quantity": 3, "unit_price": 12.5}}
Processing request of type CallToolRequest
[MCP SERVER] Executing calculate_order_total
[MCP → AGENT] JSON-RPC response: tools/call result=... isError=False
[AGENT → LLM] Returning MCP tool result
```

Expected MCP result:

```json
{
  "sku": "SKU-BOOK-001",
  "quantity": 3,
  "unit_price": 12.5,
  "subtotal": 37.5,
  "vat": 7.5,
  "total": 45.0
}
```

Expected final agent response:

```text
The order total is 45.00 including VAT.
```

Local deterministic validation of the agent loop was performed by monkeypatching `call_bedrock` to return a Bedrock-shaped `toolUse` message. Observed evidence:

```text
[LLM → AGENT] Tool selected: calculate_order_total
[AGENT → MCP] JSON-RPC request: tools/call params={"name": "calculate_order_total", "arguments": {"sku": "SKU-BOOK-001", "quantity": 3, "unit_price": 12.5}}
Processing request of type CallToolRequest
[MCP SERVER] Executing calculate_order_total
[MCP → AGENT] JSON-RPC response: tools/call result=... structuredContent={'sku': 'SKU-BOOK-001', 'quantity': 3, 'unit_price': 12.5, 'subtotal': 37.5, 'vat': 7.5, 'total': 45.0} isError=False
TOOL_RESULT_TO_LLM: ... 'status': 'success' ... 'mcp_is_error': False
```

### Failure Path

Prompt:

```text
Calculate the total for BOOK-001. Quantity is 0 and unit price is -12.50. Use the available MCP tool.
```

Expected logs:

```text
[LLM → AGENT] Tool selected: calculate_order_total
[LLM → AGENT] Tool arguments: {"sku": "BOOK-001", "quantity": 0, "unit_price": -12.5}
[AGENT → MCP] JSON-RPC request: tools/call params={"name": "calculate_order_total", "arguments": {"sku": "BOOK-001", "quantity": 0, "unit_price": -12.5}}
Processing request of type CallToolRequest
[MCP SERVER] Executing calculate_order_total
[MCP → AGENT] JSON-RPC response: tools/call result=... isError=True
[AGENT → LLM] Returning MCP tool result
```

Expected agent response:

```text
The MCP server rejected the order input: sku must start with SKU-.
```

Local deterministic validation evidence:

```text
TOOL_RESULT_TO_LLM: [{'toolResult': {'toolUseId': 'tool-failure', 'status': 'error', 'content': [{'json': {'mcp_tool_result': "[TextContent(type='text', text='Error executing tool calculate_order_total: sku must start with SKU-', annotations=None, meta=None)]", 'mcp_is_error': True}}]}}]
FINAL AGENT RESPONSE:
The MCP server rejected the order input: sku must start with SKU-.
```

This failure case is a server-side validation failure. The server receives the call, executes the tool handler, rejects invalid input, and returns an MCP error result to the agent.

## Architecture Explanation

### A. Tool Discovery and Wrapping

The agent discovers tools by calling `mcp_client.list_tools()`, which sends MCP `tools/list` over stdio. The returned metadata includes:

- tool name
- tool description
- JSON input schema

The agent converts that metadata into Bedrock Converse `toolSpec` entries. The LLM sees the schema and descriptions, not the Python implementation.

The agent receives adapters/proxies to MCP tools. It does not receive direct function references to `calculate_order_total` or `check_refund_eligibility`.

### B. MCPClient Capability

The MCP client is responsible for:

- fetching tool metadata and schemas with `tools/list`
- exposing discovered tools as callable protocol adapters through `call_tool`
- preserving the MCP boundary by never running tool logic locally
- routing tool calls over stdio to the configured MCP server process

The client does not know how order totals or refunds are calculated. It knows how to ask the MCP server to execute a named tool with JSON arguments.

### C. Execution Model

Precise trace:

1. User sends a prompt to the agent.
2. Agent sends the prompt and MCP-discovered tool schemas to the LLM.
3. LLM selects a tool and emits a `toolUse` block.
4. Agent intercepts the `toolUse` block.
5. Agent invokes the MCP adapter through `mcp_client.call_tool`.
6. MCP client sends `tools/call` over stdio transport.
7. MCP server validates input and executes the tool.
8. MCP server returns the result or error over stdio.
9. Agent wraps the MCP result as a Bedrock `toolResult`.
10. Agent passes the tool result back to the LLM.
11. LLM produces the final natural-language response.

Clarifications:

- The LLM does not execute the tool.
- The agent does not execute the tool locally.
- The MCP server is the execution authority.

### D. Tool Contract Location

- Schema location: MCP server, derived from the `@mcp.tool()` functions.
- LLM visibility: schema, name, and description only.
- Execution logic: MCP server process.

The wrapper cannot bypass MCP because it contains no business logic. Its only execution path is `mcp_client.call_tool(tool_name, tool_input)`, which routes over stdio to the server.

### E. Trust Boundary

This implementation uses stdio transport, so the boundary is a process boundary:

- Agent process: LLM orchestration, MCP discovery, tool-result forwarding.
- MCP server process: tool contract, validation, execution.

The MCP server has an independent lifecycle. It can be started, stopped, replaced, or versioned independently from the agent process.

### F. Failure Model

Failure points:

- MCP server unavailable: stdio server process cannot start or exits early.
- Transport failure: stdio read/write channel breaks.
- Schema validation failure: MCP server rejects arguments.
- Tool runtime exception: MCP server handler raises an exception.
- LLM provider failure: Bedrock credentials, model ID, or service call fails.

Propagation:

- Discovery failures stop startup or `tools/list`.
- Tool-call failures return through MCP as an error result or raise transport/session exceptions.
- The agent maps MCP `isError=True` results to Bedrock `toolResult` with `status: "error"`.
- The LLM receives the tool error and produces the final response.

### G. Security Implications

Enforced by this implementation:

- Tool execution is isolated from the agent process by a stdio process boundary.
- Tool implementations are not exposed to the LLM.
- Tool validation happens server-side.

Not enforced by this implementation:

- Identity-based authorization.
- Per-user tool permissions.
- Network TLS, because stdio is local process transport.
- Prompt-injection resistance beyond the LLM/tool schema boundary.
- Data-loss prevention for sensitive tool outputs.

For HTTP MCP transport, TLS should be used. Authentication and authorization should be enforced at the MCP server or gateway layer when tools access sensitive systems.

Risks:

- Prompt injection can cause the LLM to select an unsafe tool or unsafe arguments.
- Tools can exfiltrate data if their server-side implementation exposes sensitive resources.
- Schema-only exposure does not prove policy enforcement; it only defines the contract visible to the LLM.

### H. Operational Considerations

Deployment:

- The MCP server can be deployed independently of the agent.
- In this sample, the agent starts the server as a child process.

Scalability:

- Tool servers can scale independently when using network transports.
- Multiple MCP servers can be orchestrated by the same agent loop.

Observability:

- Client logs show discovery, selected tool name, arguments, and MCP result.
- Server logs show server startup and tool execution.
- Boundary proof comes from seeing both client-side `tools/call` and server-side `CallToolRequest`/execution logs.

Versioning:

- Tool schema drift can break LLM calls or adapter conversion.
- Contract tests should assert `tools/list` names, descriptions, required fields, and JSON schemas.

Testing:

- Contract test: call `tools/list` and validate returned schemas.
- Integration test: agent receives tool selection and routes `tools/call` through MCP.
- Failure test: send invalid arguments and assert MCP `isError=True` becomes `toolResult.status == "error"`.

### I. Suitability

Appropriate when:

- tools need a clear execution boundary
- tool teams and agent teams are separate
- tool contracts should be externalized
- tool servers need independent lifecycle or scaling
- cross-language or out-of-process tool execution is useful

Avoid when:

- the tool is trivial and in-process execution is acceptable
- low latency is more important than isolation
- no independent tool lifecycle is needed
- the deployment environment cannot supervise MCP server processes or network endpoints

## Proof Summary

Proven:

- The agent discovers tool contracts from the MCP server through `tools/list`.
- The LLM receives tool schemas, not implementation functions.
- The agent routes selected tool calls through `mcp_client.call_tool`.
- The MCP server receives `CallToolRequest` and executes `calculate_order_total`.
- Successful execution returns `isError=False` and the expected order total.
- Invalid input is rejected by the MCP server and returned as `isError=True`.
- Execution crosses a stdio process boundary.

Not proven:

- Real Bedrock tool selection in the local validation environment, because `BEDROCK_MODEL_ID` and AWS credentials were not available.
- Platform-managed boundary such as an MCP gateway.
- Identity enforcement or authorization.
- TLS, because this sample uses stdio rather than HTTP transport.
- Production-grade observability, tracing, or audit logging.

