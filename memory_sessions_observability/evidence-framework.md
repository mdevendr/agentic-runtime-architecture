# Evidence Framework

This exploration is evidence-led. Each architectural pattern should produce concrete proof, not only narrative.

## Evidence Unit

Each pattern should define:

```text
Hypothesis
Architecture shape
Infrastructure components
Runtime flow
Failure domain
Proof to build
Evidence to capture
Review questions
```

## Evidence Types

```text
Runtime evidence:
  traces, logs, spans, request/response envelopes

Security evidence:
  isolation tests, denied access attempts, permission boundaries

Memory evidence:
  memory reads/writes, retrieved chunks, graph mutations, session snapshots

Quality evidence:
  retrieval scores, eval results, groundedness checks, human corrections

Operational evidence:
  latency, cost, retries, throttling, circuit breaker events
```

## Evidence Envelope

Every proof should emit or record:

```text
run_id
correlation_id
session_id
tenant_id
user_id_hash
pattern_name
input_hash
memory_reads
memory_writes
retrieved_context_refs
tool_calls
model_calls
evaluation_results
final_output_hash
status
started_at
completed_at
```

## Review Rule

A pattern is not considered explored until we can answer:

```text
What did the agent know?
Where did that knowledge come from?
Which memory was read or written?
Which tenant boundary was enforced?
Which tools were called?
What failed, and where was it observed?
Can we replay or explain the run?
```
