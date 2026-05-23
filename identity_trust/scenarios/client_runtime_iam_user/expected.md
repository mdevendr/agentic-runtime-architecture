# client_runtime_iam_user

Second Client -> Runtime scenario.

This scenario uses IAM user long-lived access keys to sign the Runtime request with SigV4. It proves the same Runtime IAM authorization boundary as the IAM role scenario, but with static client credentials instead of STS-assumed role credentials.

Expected proof:

- IAM user with allow policy invokes Runtime successfully.
- IAM user with explicit deny fails with `AccessDeniedException`.

Setup sequence:

1. Deploy or reuse the IAM Runtime variant.
2. Export `AGENT_RUNTIME_ARN`.
3. Run `identity_setup.py --include-iam-users`.
4. Export the IAM user key variables printed by setup, or source `identity_test_users.env`.
5. Run `identity_tests.py run-scenario scenarios/client_runtime_iam_user/scenario.json`.

Reruns:

AWS only returns the secret access key when a key is created. If the dedicated test IAM users already have access keys and you need fresh local values, rerun setup with `--include-iam-users --rotate-iam-user-keys`.

This is less desirable for production clients than IAM role or IAM Identity Center because static access keys need storage, rotation, and tighter operational controls.
