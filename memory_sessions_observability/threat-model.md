# Threat Model

| Threat Category | Key Risks | Recommended Controls |
| --- | --- | --- |
| Spoofing | User/session impersonation, tenant context forgery | Signed identity context, tenant-bound sessions, audience/issuer validation |
| Tampering | Memory poisoning, tool input manipulation, trace alteration | Memory write policy, schema validation, immutable audit records |
| Repudiation | User or agent action cannot be reconstructed | Audit envelope, correlation IDs, tool call records, replay metadata |
| Information Disclosure | Cross-tenant memory leakage, prompt/log leakage | Tenant-scoped memory, redaction, retention controls, access policies |
| Denial of Service | Tool/model overload, runaway agent loops | Rate limits, circuit breakers, step budgets, concurrency controls |
| Elevation of Privilege | Agent calls tools beyond user authority | Tool authorization gateway, approval gates, scoped credentials |
