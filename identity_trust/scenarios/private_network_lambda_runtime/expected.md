# private_network_lambda_runtime

This scenario adds a network trust boundary on top of Runtime authentication:

```text
Private Lambda -> private network path -> AgentCore Runtime
```

Expected proof:

- Lambda in private subnet can invoke Runtime.
- Unauthorized identity is still denied even from the private network.
- Public/direct path is blocked or not exposed, if the selected Runtime/network mode supports that.
- Evidence includes subnet, security group, endpoint, and Lambda invocation proof.

Network boundary is not identity. It constrains where calls can come from; Runtime auth still decides who is allowed.

Setup sequence:

1. Run `private_network_setup.py` to create/reuse VPC endpoint infrastructure.
2. Source `private_network.env`.
3. Deploy `deploy_runtime.py --runtime-variant iam-private`.
4. Export `AGENT_RUNTIME_ARN_IAM_PRIVATE`.
5. Run `workload_lambda_setup.py` with private Lambda names and the private subnet/security group env.
6. Export private Lambda function name aliases.
7. Run `identity_tests.py run-scenario scenarios/private_network_lambda_runtime/scenario.json`.

The setup uses interface endpoints for:

- `com.amazonaws.<region>.bedrock-agentcore`
- `com.amazonaws.<region>.bedrock-agentcore.gateway`
- `com.amazonaws.<region>.bedrock-runtime`
- `com.amazonaws.<region>.ecr.api`
- `com.amazonaws.<region>.ecr.dkr`
- `com.amazonaws.<region>.logs`

It also uses an S3 gateway endpoint so private Runtime/Lambda image pulls can retrieve ECR-backed image layers without public internet access.

Endpoint policies are intentionally left at the default for the lab proof. A production version should tighten them to the exact Runtime, ECR repository, log group, and calling principals needed by the workload.
