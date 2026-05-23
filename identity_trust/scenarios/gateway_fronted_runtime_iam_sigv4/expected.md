# gateway_fronted_runtime_iam_sigv4

This scenario implements the first Gateway-fronted Runtime authorization mode:

```text
Client -> AgentCore Gateway -> AgentCore Runtime target
```

Gateway is the front door. Runtime remains the target. For this mode, Gateway uses its own service role to SigV4-sign the Runtime invocation.

Expected trust chain:

```text
1. Client authenticates to AgentCore Gateway.
2. Gateway accepts the inbound request according to its Gateway authorizer.
3. Gateway assumes or uses its Gateway service role.
4. Gateway signs InvokeAgentRuntime with SigV4.
5. Runtime IAM auth validates the SigV4 request.
6. IAM allows bedrock-agentcore:InvokeAgentRuntime only if the Gateway role has permission on the Runtime ARN and runtime endpoint ARN.
7. Runtime invokes the hosted agent application.
```

The setup script is:

```bash
python gateway_fronted_runtime/iam_sigv4_setup.py
```

Current implementation note:

- The script contains the deploy path for this mode.
- It first checks whether the installed boto3/botocore model supports Gateway HTTP Runtime targets.
- boto3/botocore 1.43.14 exposes `targetConfiguration.http.agentcoreRuntime`.
- If `--check-only` is used, the script prints readiness and the intended Gateway, target, and credential-provider configuration without creating resources.

Desired Runtime target shape:

```json
{
  "http": {
    "agentcoreRuntime": {
      "arn": "<runtime-arn>"
    }
  }
}
```

Desired target credential provider:

```json
[
  {
    "credentialProviderType": "GATEWAY_IAM_ROLE"
  }
]
```

Gateway service role policy:

```json
{
  "Action": "bedrock-agentcore:InvokeAgentRuntime",
  "Resource": [
    "<runtime-arn>",
    "<runtime-arn>/runtime-endpoint/*"
  ]
}
```

Test command:

```bash
python gateway_fronted_runtime/invoke_iam_sigv4.py --runtime-arn "$AGENT_RUNTIME_ARN_IAM"
```

Expected result:

- Client signs the request to the Gateway with IAM/SigV4.
- Gateway accepts the request through its IAM authorizer.
- Gateway invokes the Runtime target using `Prompt4RuntimeFrontdoorIamRole`.
- Runtime returns the hosted agent response through the Gateway target URL.
