# Multi-Tenant Agent Isolation

Multi-tenant AI systems must prevent tenant data, memory, retrieval context, tool access, and observability records from crossing boundaries.

## Core Principle

```text
Every request, memory record, retrieval chunk, tool call, trace, and audit event carries tenant_id.
```

Tenant isolation is enforced before the model sees context.

## Horizontal Flow

```text
User / Channel
  -> Identity Gateway
  -> Tenant Resolver
  -> Session Gateway
  -> Agent Runtime
  -> Memory Router
  -> RAG Retriever
  -> Tool Gateway
  -> Model Gateway
  -> Observability Pipeline
```

## Isolation Boundaries

| Boundary | Isolation Control |
| --- | --- |
| Identity | Tenant claim validated from trusted IdP/token |
| Session | Session keys partitioned by tenant_id + user_id |
| Ephemeral memory | Prompt-local only, discarded after turn |
| Session memory | Partitioned by tenant_id/session_id |
| Long-term memory | Partitioned by tenant_id/user_id and consent policy |
| RAG corpus | Tenant-aware metadata filters and index partitioning |
| Tools | Tenant-scoped credentials and authorization policy |
| Observability | Tenant-tagged traces with redaction and access controls |
| Audit | Immutable tenant-tagged event envelope |

## Tenant Models

### Model A: Shared Runtime, Shared Stores, Logical Isolation

```text
Tenants
  -> shared runtime
  -> shared stores
  -> tenant_id partition key and policy filters
```

Use when:

```text
low to medium tenant risk
cost efficiency matters
strong app-level controls exist
```

Controls:

```text
tenant_id partition keys
row/item-level authorization
mandatory tenant filters
automated isolation tests
```

### Model B: Shared Runtime, Dedicated Tenant Stores

```text
Tenants
  -> shared runtime
  -> per-tenant memory/vector stores
```

Use when:

```text
tenants require stronger data isolation
retrieval corpus is sensitive
tenant-specific retention policies exist
```

Controls:

```text
per-tenant stores or indexes
per-tenant encryption keys
per-tenant backup/retention policies
```

### Model C: Dedicated Tenant Runtime and Stores

```text
Tenant
  -> dedicated runtime
  -> dedicated memory stores
  -> dedicated tool credentials
```

Use when:

```text
regulated tenants require hard isolation
high sensitivity data is processed
tenant-specific compliance controls are required
```

Controls:

```text
separate runtime deployment
separate IAM roles
separate stores
separate KMS keys
separate observability access boundaries
```

## Memory Isolation

```text
Ephemeral memory:
  never shared; exists only inside current turn

Session memory:
  tenant_id + session_id scoped

Long-term memory:
  tenant_id + user_id scoped
  writes require policy and consent

RAG memory:
  tenant filters applied before retrieval result reaches prompt

Audit memory:
  tenant-tagged and access-controlled
```

## RAG Isolation

RAG retrieval must apply tenant isolation before context assembly:

```text
query
  -> tenant filter
  -> access policy filter
  -> retrieval
  -> reranking
  -> context pack
```

Do not rely on prompt instructions to separate tenants.

## Tool Isolation

Tools should be invoked through a tenant-aware gateway:

```text
agent request
  -> tenant authorization check
  -> tool policy
  -> scoped credential
  -> tool invocation
  -> tenant-tagged result
```

## Observability Isolation

Every trace span should include:

```text
tenant_id
user_id_hash
session_id
conversation_id
agent_id
tool_name
memory_store
retrieval_index
correlation_id
```

Sensitive trace fields should be redacted or tokenized.

## Isolation Tests

Required negative tests:

```text
Tenant A cannot read Tenant B session memory
Tenant A cannot retrieve Tenant B RAG documents
Tenant A cannot invoke Tenant B tool credentials
Tenant A traces are not visible to Tenant B operators
Tenant A long-term memory cannot be injected into Tenant B prompt
```

