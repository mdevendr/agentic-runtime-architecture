# Production Hardening Evidence

This folder maps the article's production hardening guidance to concrete repository evidence. The baseline pattern folders stay intentionally small. The helpers here show how those patterns can be hardened without hiding the core boundary mechanics.

## Evidence Map

| Article topic | Repo evidence | Purpose |
| --- | --- | --- |
| Resumable Runtime state | `shared/state_store.py` | Defines a persisted Converse execution frame with pending `toolUseId` state. |
| Mutation-boundary idempotency | `shared/idempotency.py` | Generates deterministic idempotency keys from `session_id`, `message_id`, and `tool_use_id`. |
| Correlation and trace context | `shared/execution_context.py` | Carries session, message, tool, correlation, and trace identifiers across boundaries. |
| Runaway tool loop isolation | `shared/circuit_breaker.py` | Bounds consecutive and repeated tool calls per session. |
| Static cached schema catalog | `schema_catalog/build_catalog.py` and `schema_catalog/catalog.example.json` | Compiles Gateway tool schemas into a versioned, hash-verified catalog. |
| Identity substitution hardening | `identity_trust/caller_context_assertion.py` | Demonstrates a signed caller-context assertion for substitution modes. |
| Runtime-side caller attribution | `identity_trust/caller_context_demo/` | Demonstrates both client-signed payload assertions and Gateway REQUEST-interceptor-signed header assertions while Runtime-facing identity is substituted. |

## Baseline vs Hardened Patterns

The existing folders demonstrate the boundary progression:

- direct in-process tools
- MCP process boundary
- AgentCore Runtime hosting
- AgentCore Gateway mediation
- Runtime inbound identity and trust

The hardening helpers demonstrate production controls that are usually layered on top:

- idempotency before mutating state
- durable execution frames for resumable Converse loops
- loop budgets and circuit breakers independent of model behavior
- static schema catalogs instead of per-session dynamic schema hydration
- caller-context preservation when Gateway substitutes Runtime-facing identity

These helpers are intentionally dependency-light. Production deployments should replace in-memory examples with managed stores such as DynamoDB conditional writes, Redis-style `SETNX`, centralized trace propagation, managed signing keys, and organization-specific policy enforcement.

## Examples

- `resumable_loop_example.py` shows how a `toolUse` frame can be persisted, rehydrated, and continued with a matching `toolResult`.
- `issue_refund_target.py` shows mutation-boundary idempotency enforcement before a target commits a side effect.
- `../identity_trust/caller_context_demo/runtime_boundary_evidence.py` shows good and bad caller-context verification decisions at the Runtime application boundary.
- `../identity_trust/caller_context_demo/invoke_with_caller_context.py` shows Runtime-side verification of client-signed caller context.
- `../identity_trust/caller_context_demo/setup_gateway_with_interceptor.py` and `invoke_gateway_header_client.py` show the Gateway-signed header variant using a REQUEST interceptor and Runtime header allowlist.

Run locally:

```bash
python production-hardening/resumable_loop_example.py
python identity_trust/caller_context_demo/invoke_with_caller_context.py
python identity_trust/caller_context_demo/tamper_test.py
```
