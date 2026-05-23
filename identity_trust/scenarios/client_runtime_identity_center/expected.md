# client_runtime_identity_center

Third Client -> Runtime scenario.

This scenario uses IAM Identity Center as the human federation source. The local AWS CLI profile receives temporary credentials for an `AWSReservedSSO_*` role, then signs `InvokeAgentRuntime` with SigV4.

Expected proof:

- IAM Identity Center permission set with allow policy invokes Runtime successfully.
- Permission set with explicit deny fails with `AccessDeniedException`.
- Local SSO profiles are configured for both permission sets.
- Evidence includes `aws sts get-caller-identity` output showing `AWSReservedSSO_*` assumed-role ARNs.

This still proves IAM/SigV4 runtime auth. Identity Center is the human federation source for the AWS role credentials.

Setup sequence:

1. Deploy or reuse the IAM Runtime variant.
2. Export `AGENT_RUNTIME_ARN`.
3. Run `identity_center_setup.py` to create allow/deny permission sets and assign them to the Identity Center user.
4. Configure two AWS CLI SSO profiles with the permission set names printed by setup.
5. Run `aws sso login` for both profiles.
6. Run `identity_tests.py run-scenario scenarios/client_runtime_identity_center/scenario.json`.

The default permission set names are:

- `Prompt4RuntimeInvokeAllow`
- `Prompt4RuntimeInvokeDeny`

The default scenario profile names are:

- `prompt4-identity-center-allow`
- `prompt4-identity-center-deny`
