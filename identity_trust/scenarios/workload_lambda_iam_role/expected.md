# workload_lambda_iam_role

Fourth scenario.

This scenario moves the caller from a local client to an AWS workload. The Lambda function uses its execution role credentials to sign `InvokeAgentRuntime` with SigV4.

Expected proof:

- Lambda execution role can invoke the IAM Runtime.
- Lambda execution role without `bedrock-agentcore:InvokeAgentRuntime` fails.
- Evidence includes Lambda logs and Runtime response.

This covers AWS workload trust into Runtime. ECS, EKS, and EC2 workload roles are discussed in the article as equivalent IAM/SigV4 workload variants, but not implemented separately.

Setup sequence:

1. Deploy or reuse the IAM Runtime variant.
2. Export `AGENT_RUNTIME_ARN_IAM`.
3. Run `workload_lambda_setup.py` to create allow and deny Lambda workload functions.
4. Source `workload_lambda.env`.
5. Run `identity_tests.py run-scenario scenarios/workload_lambda_iam_role/scenario.json`.

The setup creates:

- `prompt4-runtime-workload-allow`
- `prompt4-runtime-workload-deny`
- `Prompt4LambdaRuntimeWorkloadAllow`
- `Prompt4LambdaRuntimeWorkloadDeny`
