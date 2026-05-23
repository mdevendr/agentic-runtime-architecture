# gateway_fronted_runtime

This scenario documents an alternate inbound path to AgentCore Runtime:

```text
Client -> AgentCore Gateway -> AgentCore Runtime target
```

This is different from the MCP Gateway tool path:

```text
Runtime -> MCP Client -> AgentCore Gateway -> Lambda/API tool target
```

The Gateway-fronted Runtime path makes Gateway the first front-door trust boundary. Gateway can centralize inbound auth, observability, and routing to one or more Runtime agents. The Runtime is the target behind Gateway.

Expected proof:

- Gateway exists as a Runtime front-door Gateway, separate from the MCP tools Gateway.
- Gateway target points to the AgentCore Runtime ARN.
- Client invokes the Gateway route.
- Request reaches the configured AgentCore Runtime.
- Evidence explains which identity was validated at Gateway and which credentials Gateway used toward Runtime.

Implementation note:

- `runtime_gateway_frontdoor_setup.py` checks the installed `bedrock-agentcore-control` API model.
- The concrete mode scripts under `gateway_fronted_runtime/` use the Runtime HTTP target shape exposed by boto3/botocore 1.43.14.
- If the global AWS CLI model lags, use the Python setup scripts as the source of truth.

Desired target configuration shape:

```json
{
  "http": {
    "agentcoreRuntime": {
      "arn": "<runtime-arn>"
    }
  }
}
```

Do not attach this to the existing MCP protocol Gateway used for tool execution. Keep it as a separate Gateway-front-door pattern.

Concrete modes:

- `GATEWAY_IAM_ROLE`: Runtime authorizes the Gateway service role.
- `CALLER_IAM_CREDENTIALS`: Runtime authorizes the original caller IAM identity.
- `OAUTH`: Runtime authorizes a Gateway-obtained JWT from AgentCore Identity and Cognito.
- `JWT_PASSTHROUGH`: Runtime authorizes the same JWT Gateway validated inbound.
