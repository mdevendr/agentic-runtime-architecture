# client_runtime_iam_role

This is the first Client -> AgentCore Runtime identity scenario.

The client starts with an existing AWS principal, assumes a dedicated IAM role with STS, then calls `InvokeAgentRuntime` with SigV4 temporary credentials.

Expected evidence:

- `runtime_authorized_with_allow_role` succeeds using `PROMPT4_CLIENT_RUNTIME_IAM_ROLE_ALLOW_ARN`.
- `runtime_denied_with_deny_role` fails with `AccessDeniedException` using `PROMPT4_CLIENT_RUNTIME_IAM_ROLE_DENY_ARN`.
- Evidence is written under `evidence/client_runtime_iam_role/<timestamp>/`.

Required environment:

```bash
export AWS_REGION="eu-west-2"
export AGENT_RUNTIME_ARN="<runtime-arn>"
```

Create identities:

```bash
python identity_setup.py
```

Export the values printed by the setup script:

```bash
export PROMPT4_CLIENT_RUNTIME_IAM_ROLE_ALLOW_ARN="<allow-role-arn>"
export PROMPT4_CLIENT_RUNTIME_IAM_ROLE_DENY_ARN="<deny-role-arn>"
```

Run:

```bash
python identity_tests.py run-scenario scenarios/client_runtime_iam_role/scenario.json
```
