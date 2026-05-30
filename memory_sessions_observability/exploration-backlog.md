# Exploration Backlog

## 1. Layered Memory Single Agent

Hypothesis:

```text
Separating scratchpad, episodic, and semantic memory reduces latency and keeps long-term memory writes out of the live response path.
```

Proof to build:

```text
Single agent runtime with session memory and async semantic consolidation worker.
```

Evidence to capture:

```text
session trace
short-term state
emitted episodic event
async consolidation output
semantic memory write
latency with and without async consolidation
```

## 2. Graph-Relational Memory

Hypothesis:

```text
Graph memory improves long-running entity relationship recall compared with flat vector retrieval alone.
```

Proof to build:

```text
Entity extraction -> graph merge -> ego-network retrieval -> prompt context pack.
```

Evidence to capture:

```text
extracted entities
created/merged nodes
created/merged relationships
graph query result
context pack
entity resolution failures
```

## 3. Governed RAG

Hypothesis:

```text
Tenant-filtered retrieval with citations can prevent cross-tenant leakage while improving answer grounding.
```

Proof to build:

```text
Two-tenant document corpus with enforced tenant filters and citation capture.
```

Evidence to capture:

```text
retrieval query
tenant filter
retrieved document IDs
retrieval scores
reranker scores
citation coverage
negative cross-tenant retrieval test
```

## 4. Async Agent Trace and Evaluation

Hypothesis:

```text
Async evaluation can detect loop recursion, tool misuse, and retrieval drift without blocking live execution.
```

Proof to build:

```text
Agent run emits OpenTelemetry-style spans; evaluator consumes traces and flags failures.
```

Evidence to capture:

```text
trace spans
tool call signatures
loop detection result
drift score
eval decision
circuit breaker event
```

## 5. HITL Intercept

Hypothesis:

```text
A runtime can pause risky tool execution, serialize state, obtain approval, and resume without holding compute open.
```

Proof to build:

```text
Protected tool call triggers state freeze, review record, approval callback, and resume.
```

Evidence to capture:

```text
suspend_id
serialized state
pending tool use
review decision
resume event
final tool result
idempotency key
```

## 6. Supervisor / Worker Topology

Hypothesis:

```text
A supervisor can isolate global planning from stateless worker execution and reduce worker memory exposure.
```

Proof to build:

```text
Supervisor decomposes task and invokes stateless retrieval/tool workers with minimal context.
```

Evidence to capture:

```text
supervisor plan
worker input envelope
worker output envelope
context minimization check
failure propagation trace
```

## 7. Peer-to-Peer Agent Mesh

Hypothesis:

```text
Event-driven agent choreography improves resilience but increases trace reconstruction complexity.
```

Proof to build:

```text
Agents publish/consume typed events through a local event bus abstraction.
```

Evidence to capture:

```text
event contracts
event causality chain
agent spans
queue retry behavior
partial failure behavior
```

## 8. Cross-Tenant Hub-and-Spoke

Hypothesis:

```text
A shared hub runtime can safely serve multiple tenants when memory, retrieval, tools, credentials, and traces are tenant-scoped at every boundary.
```

Proof to build:

```text
Shared runtime with two tenants, isolated memory stores, isolated RAG filters, and tenant-scoped tool credentials.
```

Evidence to capture:

```text
tenant envelope
allowed tenant retrieval
denied cross-tenant retrieval
tool credential selection
trace tenant tags
negative isolation tests
```
