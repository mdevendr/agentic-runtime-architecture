# gateway_fronted_runtime_caller_iam

This scenario implements the second Gateway-fronted Runtime authorization mode:

```text
Client -> AgentCore Gateway -> AgentCore Runtime target
```

For this mode, Gateway signs the Runtime target request using caller IAM credentials. Runtime authorizes the original caller identity, not the Gateway service role.

Expected trust chain:

```text
1. Client signs request to Gateway target URL with caller IAM credentials.
2. Gateway accepts the inbound request through its AWS_IAM authorizer.
3. Gateway routes to the Runtime target.
4. Gateway signs InvokeAgentRuntime using caller IAM credentials.
5. Runtime IAM auth validates the SigV4 request.
6. Runtime evaluates IAM policy on the original caller identity.
7. Runtime invokes the hosted agent application if the caller is allowed.
```

The caller must be allowed to invoke both:

```text
bedrock-agentcore:InvokeGateway
bedrock-agentcore:InvokeAgentRuntime
```

Desired target credential provider:

```json
[
  {
    "credentialProviderType": "CALLER_IAM_CREDENTIALS",
    "credentialProvider": {
      "iamCredentialProvider": {
        "service": "bedrock-agentcore",
        "region": "eu-west-2"
      }
    }
  }
]
```

Setup command:

```bash
python gateway_fronted_runtime/caller_iam_setup.py --runtime-arn "$AGENT_RUNTIME_ARN_IAM"
```

To update the existing scenario 1 allow role with the permissions needed for this Gateway mode:

```bash
python gateway_fronted_runtime/caller_iam_setup.py \
  --runtime-arn "$AGENT_RUNTIME_ARN_IAM" \
  --caller-allow-role-arn "$PROMPT4_CLIENT_RUNTIME_IAM_ROLE_ALLOW_ARN"
```

