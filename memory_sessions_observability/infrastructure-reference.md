# Infrastructure Reference

This document makes the architecture concrete: how many agents, gateways, stores, tools, connections, and observability components exist in each option.

All options assume tenant context is resolved before memory, retrieval, or tool access:

```text
identity token -> tenant resolver -> tenant_id on every runtime envelope
```

## Option 1: Single Agent, Session Memory Only

Use this for a simple copilot or assistant where the agent needs continuity inside a session but no durable memory.

```text
User / Channel
  -> Identity Gateway
  -> Tenant Resolver
  -> Session Gateway
  -> Agent Runtime
  -> RAG Retriever
  -> Model Gateway
  -> Response

Agent Runtime
  -> Session Store
  -> Retrieval / Vector Store
  -> Tool Gateway
  -> Observability Pipeline
```

### Components

| Area | Count | Components |
| --- | ---: | --- |
| Agents | 1 | Primary conversational agent |
| Gateways | 3 | Identity Gateway, Session Gateway, Model Gateway |
| Memory stores | 2 | Session Store, Retrieval/Vector Store |
| Tool gateways | 1 | Tool Gateway |
| Observability | 1 pipeline | Trace/log/metrics collector |

### Memory handling

```text
Ephemeral memory:
  current prompt, recent turns, transient tool results

Session memory:
  active task state, selected entities, user choices, pending approvals

Long-term memory:
  not used

Retrieval memory:
  read-only RAG knowledge base
  retrieved chunks are attached to the prompt for grounding
```

### RAG handling

```text
Document ingestion:
  documents -> chunking -> embeddings -> vector index -> metadata store

Query-time retrieval:
  user query -> retrieve top-k chunks -> tenant/security filters -> prompt context
```

### Tool types

```text
Read-only tools:
  search, lookup, policy retrieval, catalog query

Low-risk action tools:
  draft response, summarize, classify, validate
```

### Connectivity

```text
User -> API Gateway / App Gateway
Runtime -> Model endpoint over private or approved HTTPS path
Runtime -> Session Store
Runtime -> Tool Gateway
Tool Gateway -> approved internal APIs
```

### Observability

Capture:

```text
session_id
tenant_id
conversation_id
correlation_id
model latency
token usage
tool calls
retrieved document IDs
retrieval scores
errors
policy decisions
```

Use observability to answer:

```text
What happened in this session?
Which tool was called?
What did the model spend?
Where did latency occur?
Which documents grounded the answer?
```

## Option 2: Multi-Agent Runtime With Shared Session State

Use this when the system has specialist agents but all work happens inside a bounded user session.

```text
User / Channel
  -> Identity Gateway
  -> Tenant Resolver
  -> Session Gateway
  -> Orchestrator Agent
      -> Planner Agent
      -> Retrieval Agent
      -> Tool-Use Agent
  -> Model Gateway
  -> Response Composer

Shared Runtime
  -> Session Store
  -> Retrieval Store
  -> RAG Ingestion Pipeline
  -> Tool Gateway
  -> Observability Pipeline
```

### Components

| Area | Count | Components |
| --- | ---: | --- |
| Agents | 3-4 | Orchestrator, Planner, Retrieval, Tool-Use |
| Gateways | 4 | Identity Gateway, Session Gateway, Tool Gateway, Model Gateway |
| Memory stores | 2 | Session Store, Retrieval/Vector Store |
| Tool gateways | 1 | Central Tool Gateway |
| Observability | 1 pipeline + trace store | Distributed traces, eval events, audit records |

### Agent roles

```text
Orchestrator Agent:
  owns turn lifecycle and final response

Planner Agent:
  decomposes task, decides tool sequence

Retrieval Agent:
  rewrites queries and fetches context from vector/document stores

Tool-Use Agent:
  invokes approved tools through Tool Gateway
```

### Memory handling

```text
Ephemeral memory:
  per-agent scratchpad, not persisted

Session memory:
  shared task state, plan, decisions, pending approvals

Retrieval memory:
  vector/document store, read-only at runtime unless ingestion approved

Long-term memory:
  not used, or write-disabled by default
```

### RAG handling

```text
Ingestion path:
  source documents
  -> classification / metadata extraction
  -> chunking
  -> embeddings
  -> vector store
  -> document/version metadata

Runtime path:
  user intent
  -> Retrieval Agent
  -> tenant/security-filtered vector search
  -> reranking
  -> cited context pack
  -> Prompt Builder
```

Runtime conversations should not silently write into the RAG corpus. Corpus updates should use an approved ingestion path.

### Tool types

```text
Read tools:
  knowledge search, customer/account lookup, ticket lookup

Validation tools:
  schema validation, policy checks, data quality checks

Action tools:
  create ticket, send notification, update workflow state

Approval-required tools:
  external side effects, production changes, financial/refund actions
```

### Connectivity

```text
Agents do not call tools directly.
Agents -> Tool Gateway -> internal APIs / SaaS APIs.

Agents do not call models directly.
Agents -> Model Gateway -> model providers.

Session state is shared through Session Store.
Agents do not pass hidden state peer-to-peer without trace capture.
```

### Observability

Capture:

```text
parent_trace_id
tenant_id
agent_span_id
agent role
prompt template ID
retrieval query and document IDs
retrieval scores and reranker scores
context pack version
tool name and arguments hash
approval decision
model/token metrics
final response hash
```

Use observability to answer:

```text
Which agent made the decision?
Which documents were retrieved?
Were retrieved documents allowed for this tenant/user?
Which tool caused the failure?
Was human approval required?
Can the session be replayed?
```

## Option 3: Governed Memory Platform

Use this for production enterprise copilots or agentic systems that need durable memory, strict tenant isolation, audit, and replay.

```text
User / Channel
  -> Identity Gateway
  -> Tenant Resolver
  -> Session Gateway
  -> Policy & Consent Gateway
  -> Agent Orchestrator
      -> Specialist Agents
  -> Memory Router
      -> Ephemeral Context
      -> Session Store
      -> Long-Term Memory Store
      -> Retrieval / Vector Store
  -> RAG Governance Pipeline
  -> Tool Gateway
  -> Model Gateway
  -> Response Composer
  -> Observability + Audit Pipeline
```

### Components

| Area | Count | Components |
| --- | ---: | --- |
| Agents | 4-7 | Orchestrator plus specialist agents |
| Gateways | 5 | Identity, Session, Policy/Consent, Tool, Model |
| Memory stores | 4 | Ephemeral context, Session Store, Long-Term Memory, Retrieval/Vector Store |
| Tool gateways | 1 central, optional domain gateways | Central policy enforcement with domain adapters |
| Observability | 2 stores | Operational traces + governed audit/replay store |

### Agent roles

```text
Orchestrator Agent:
  controls lifecycle and response

Planner Agent:
  creates task plan and step budget

Retrieval Agent:
  performs context retrieval

Memory Agent:
  proposes memory reads/writes

Tool Agent:
  executes approved tool calls

Safety/Policy Agent:
  checks policy and consent constraints

Evaluation Agent:
  scores outcome quality after execution
```

### Memory handling

```text
Ephemeral context:
  prompt-local, discarded after turn

Session memory:
  task-local, expires with session or retention policy

Long-term memory:
  durable user/tenant facts, consent-controlled, auditable writes

Retrieval memory:
  enterprise/document knowledge, indexed, versioned, filtered, and cited

Audit memory:
  immutable evidence of decisions, tool calls, and retrieved references
```

### RAG handling

```text
Governed ingestion:
  source systems
  -> data classification
  -> PII/secret detection
  -> access-control metadata
  -> chunking
  -> embedding
  -> index versioning
  -> approval/promote to searchable corpus

Governed retrieval:
  query rewrite
  -> tenant/user access filters
  -> hybrid search
  -> reranking
  -> policy filter
  -> context pack with citations
  -> prompt builder
```

RAG corpus changes should be versioned and observable:

```text
document_id
source_system
classification
chunk_id
embedding_model
index_version
access_policy
ingestion_timestamp
```

### Memory write policy

All durable memory writes require:

```text
identity context
tenant context
memory type
source evidence
consent basis
expiry/retention
confidence score
audit record
```

### Tool types

```text
Read-only tools:
  search, document retrieval, metadata lookup

Computational tools:
  calculators, validators, classifiers

Workflow tools:
  ticket creation, approval requests, case updates

Data mutation tools:
  CRM/update actions, database updates, production changes

External action tools:
  email, payment/refund, deployment, SaaS administration
```

### Tool risk tiers

```text
Tier 0:
  no tool, model-only response

Tier 1:
  read-only tools

Tier 2:
  internal low-risk state updates

Tier 3:
  external side effects, requires approval

Tier 4:
  privileged/production action, requires approval + break-glass audit
```

### Connectivity

```text
Runtime -> Memory Router -> approved memory stores
Runtime -> Tool Gateway -> tools using scoped credentials
Runtime -> Model Gateway -> model endpoints
Runtime -> Observability Pipeline -> trace/audit stores
```

Connectivity rules:

```text
No direct agent-to-database access.
No direct agent-to-SaaS access.
No direct model-to-tool access.
All tool calls pass through authorization and trace capture.
All memory writes pass through policy and consent checks.
```

### Observability

Operational telemetry:

```text
latency
tenant_id
token usage
model errors
tool errors
retrieval latency
cost
rate limits
```

Quality telemetry:

```text
retrieval precision
retrieval recall proxy
retrieval hit rate
retrieval zero-result rate
answer groundedness
source citation coverage
tool success rate
human correction rate
evaluation score
```

Governance telemetry:

```text
memory read/write events
consent basis
policy decisions
approval outcomes
safety events
redaction events
tenant boundary checks
```

Use observability to answer:

```text
What did the agent know?
Where did that knowledge come from?
Which RAG corpus version was used?
Which memory was read or written?
Which tool was invoked and under whose authority?
Was the output grounded?
Can we replay the decision path?
```
