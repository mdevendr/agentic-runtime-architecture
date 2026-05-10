# AI Agent Tooling Architecture Patterns
**Direct Tools, MCP Boundaries, and Multi-Server Tool Orchestration**

Tool use is one of the most important architectural decisions in AI agent systems. It determines where execution happens, where contracts live, how failures propagate, and how much isolation exists between the agent, the model, and the systems being acted upon.

This repository presents a progressive set of **proof-driven AI agent tooling architecture patterns**, starting with local in-process tool execution and evolving toward Model Context Protocol (MCP) based execution boundaries.

The objective is to help architects and platform teams reason about **execution authority, trust boundaries, validation ownership, observability, and operational blast radius** when designing tool-capable AI agents.

---

**Mahesh Devendran** — Multi Cloud Architect & DevOps Leader | AWS | Azure | GCP | Kubernetes | CKA & Terraform Certified | Gen AI & Automation

**LinkedIn:** https://www.linkedin.com/in/mahesh-devendran

---

## Architectural Context

An AI model does not execute tools. It selects a tool and proposes arguments.

The architecture around the model determines what happens next:

- whether the agent executes a Python function directly
- whether execution crosses a protocol boundary
- whether tool schemas are local or externalized
- whether validation happens inside the agent or inside a tool server
- whether failures are local process failures or isolated server-side failures

This repository focuses on those execution boundaries rather than on prompt engineering alone.

---

## Architectural Principles

Across the patterns, the following principles are held constant:

- **The LLM selects tools; it does not execute them.**
- **Tool contracts must be explicit** through schema, description, and validation.
- **Execution authority must be identifiable** in code and logs.
- **Failure evidence matters** as much as successful execution.
- **Boundary placement is an architectural decision**, not an implementation detail.
- Each pattern changes the execution boundary deliberately so the trade-off is observable.

---

## Patterns Covered

### Pattern 1 — Direct Tools Architecture
**In-Process Tool Execution**

Direct Tools is the baseline pattern. The agent exposes local Python functions to the LLM as callable tools. Tool schemas and validation live inside the agent code, and execution happens in the same Python process as the agent loop.

![Direct Tools architecture](direct_vs_mcp/architechture/DirectTooling.png)

**Architectural focus:**  
Establish the baseline execution model where there is no tool protocol boundary. The agent framework validates inputs and executes local Python handlers in-process.

**Implementation:**  
[direct_vs_mcp/direct-tools-architecture](direct_vs_mcp/direct-tools-architecture)

**Proof points:**

- LLM selects `calculate_order_total`.
- Agent executes a local Python function.
- Pydantic enforces local validation.
- Invalid SKU input fails inside the agent process.
- Logs show tool selection, raw input, validation, execution, and result.

---

### Pattern 2 — MCP Server Architecture
**Explicit Tool Execution Boundary**

MCP introduces a protocol boundary between the agent and tool execution. The agent connects to MCP servers using stdio transport, discovers tools using `tools/list`, and routes tool calls using `tools/call`.

In the current implementation, a single orchestrating agent connects to two independent MCP servers:

- Order MCP server owns `calculate_order_total`
- Refund MCP server owns `check_refund_eligibility`

![MCP tooling architecture](direct_vs_mcp/architechture/MCPTooling.png)

**Architectural focus:**  
Prove that the agent becomes an orchestration layer while MCP servers become execution authorities. Tool schemas, validation, and execution move outside the agent process and behind explicit MCP stdio boundaries.

**Implementation:**  
[direct_vs_mcp/mcp-server-architecture](direct_vs_mcp/mcp-server-architecture)

**Proof points:**

- Agent starts separate MCP server processes.
- Agent creates separate MCP client sessions.
- Agent discovers tools from each server.
- Agent builds a `tool_name -> owning MCP session` map.
- Order tool execution crosses the ORDER MCP boundary.
- Refund tool execution crosses the REFUND MCP boundary.
- Refund validation failure is isolated while order execution succeeds.

---

## Repository Structure

```text
direct_vs_mcp/
  architechture/
    DirectTooling.png
    MCPTooling.png

  direct-tools-architecture/
    direct_tools_agent.py
    README.md

  mcp-server-architecture/
    mcp_agent.py
    mcp_order_server.py
    mcp_refund_server.py
    README.md
```

---

## Running the Examples

### Direct Tools

```powershell
cd direct_vs_mcp/direct-tools-architecture
pip install boto3 pydantic
$env:AWS_REGION = "eu-west-2"
$env:BEDROCK_MODEL_ID = "<bedrock-model-id-that-supports-tool-use>"

python direct_tools_agent.py success
python direct_tools_agent.py failure
```

### MCP / Multi-MCP

```powershell
cd direct_vs_mcp/mcp-server-architecture
pip install mcp boto3
$env:AWS_REGION = "eu-west-2"
$env:BEDROCK_MODEL_ID = "<bedrock-model-id-that-supports-tool-use>"

python mcp_agent.py success
python mcp_agent.py failure
```

---

## Observing Behaviour

The examples are intentionally instrumented with logs so the execution model is visible.

For Direct Tools, observe:

- tool selected by the LLM
- raw tool input
- local validation result
- local Python function execution
- returned tool result

For MCP, observe:

- MCP server process startup
- `tools/list` discovery per server
- tool ownership mapping
- routing to the owning MCP server
- server-side execution logs
- MCP error propagation

The goal is not merely to return an answer. The goal is to prove where execution happened.

---

## Key Comparison

| Concern | Direct Tools | MCP / Multi-MCP |
|---|---|---|
| Tool execution | Agent process | MCP server process |
| Tool schema location | Agent code | MCP server |
| Validation location | Agent code | MCP server |
| Boundary | None for tools | stdio process boundary |
| Agent role | Orchestrator and executor | Orchestrator only |
| Tool ownership | Coupled to agent | Externalized to server |
| Failure domain | Shared with agent | Isolated per MCP server |
| Operational fit | Simple trusted tools | Boundary-aware tool systems |

---

## Usage Note

The code in this repository is provided for **architectural exploration and design validation**. It is intentionally minimal and scoped to demonstrate tooling execution patterns, not to provide a production-ready agent platform.

Production systems must add appropriate authentication, authorization, secrets handling, deployment hardening, observability, cost controls, model governance, and operational runbooks.

---

## Disclaimer

These examples are educational reference implementations. They are not prescriptive production architectures and must be adapted to organizational security, compliance, availability, and operational requirements.

