# Direct Tools Architecture: AI Agent Implementation

This implementation demonstrates a Direct Tools architecture for AI agents, where tools are local Python functions executed in-process by the agent framework. Bedrock receives the tool schema and uses it to guide tool selection and argument generation. However, hard enforcement of the tool contract is performed by the local Python runtime using Pydantic validation before the handler executes.

## Code

The Python implementation is in `direct_tools_agent.py`. It defines two local Python tools (`calculate_order_total` and `check_refund_eligibility`) with Pydantic schemas for input validation, registers them in a tool registry, and includes test prompts for success and failure cases. Logging is included to show tool selection, input validation, execution, and results.

Key excerpts:
- **Tool schemas**: `OrderTotalInput` and `RefundEligibilityInput` use Pydantic with field validators (e.g., SKU must start with "SKU-", order_id with "ORD-").
- **Tool handlers**: Local functions that execute in-process.
- **Tool registry**: `TOOLS` dict maps names to descriptions, schemas, and handlers.
- **Agent loop**: Uses AWS Bedrock to converse with the LLM, handling tool use in a loop.
- **Test prompts**: "success" for valid tool invocation, "failure" for invalid inputs.

**Assumption**: AWS Bedrock is configured with a model supporting tool use (e.g., Claude 3.5 Sonnet). Without `BEDROCK_MODEL_ID` and AWS credentials, the script raises a RuntimeError.

## Expected Test Output

Since the code requires AWS Bedrock (not available in this environment), outputs are described based on code analysis and expected behavior. Actual execution would require setting `AWS_REGION` and `BEDROCK_MODEL_ID`.

### Successful Output (python direct_tools_agent.py success)
- **Prompt**: "Calculate the total price for SKU-BOOK-001. Quantity is 3 and unit price is 12.50. Use the available tool."
- **Expected Logs**:
  - Tool selected by LLM: calculate_order_total
  - Raw tool input: {"sku": "SKU-BOOK-001", "quantity": 3, "unit_price": 12.50}
  - Tool input validation passed
  - Tool execution result: {"sku": "SKU-BOOK-001", "quantity": 3, "unit_price": 12.50, "subtotal": 37.5, "vat": 7.5, "total": 45.0}
- **Expected Agent Response**: A natural language summary incorporating the tool result, e.g., "The total price for SKU-BOOK-001 with quantity 3 and unit price 12.50 is 45.0, including VAT."

### Failure Output (python direct_tools_agent.py failure)
- **Prompt**: "Calculate the total price for item BOOK-001. Quantity is 0 and unit price is -12.50. Use the available tool."
- **Expected Logs**:
  - Tool selected by LLM: calculate_order_total
  - Raw tool input: {"sku": "BOOK-001", "quantity": 0, "unit_price": -12.50} (assuming LLM hallucinates invalid inputs)
  - Tool input validation failed (ValidationError details logged)
- **Expected Agent Response**: Error message indicating validation failure, e.g., "I encountered an error: ValidationError - sku must start with 'SKU-', quantity must be >=1, unit_price must be >0."

**Assumption**: The LLM correctly selects tools and provides inputs as per the prompt. In failure cases, invalid inputs trigger Pydantic ValidationError.

## Architecture Explanation

### A. Tool Contract
- **Definition**: Tool name, description, schema, and parameter types are defined in the `TOOLS` dict. Names are strings (e.g., "calculate_order_total"), descriptions are plain text, schemas are Pydantic models (`OrderTotalInput`, `RefundEligibilityInput`), and types are enforced via Pydantic fields (e.g., `sku: str`, `quantity: int`).
- **Validation Enforcement**:
  - Python typing: Used in function signatures and Pydantic fields for type hints.
  - Pydantic/schema generation: Models auto-generate JSON schemas via `model_json_schema()` for Bedrock's toolConfig.
  - Custom validation: `@field_validator` decorators enforce business rules (e.g., SKU prefix).
  - Framework runtime checks: Bedrock's converse API validates inputs against the schema before tool execution.
- **Invalid Arguments Handling**: If the LLM passes invalid args (e.g., wrong types or failing validators), Pydantic raises ValidationError during `model_validate()`. This is caught, logged, and returned as an error to the agent, surfacing as a user-visible failure.

### B. Execution Model
- **Step-by-Step Flow**:
  1. User provides prompt.
  2. Agent calls Bedrock converse with toolConfig.
  3. LLM analyzes prompt, selects tool, and generates toolUse block with inputs.
  4. Agent framework (local Python code) parses toolUse, validates inputs via Pydantic, executes handler in-process.
  5. Result is formatted as toolResult and sent back to LLM.
  6. LLM generates final response incorporating result.
- **Clarifications**:
  - LLM does NOT execute the tool; it only selects and provides inputs.
  - Agent framework executes the tool in-process (same Python process).
  - Execution shares the process and memory boundary—no serialization or network calls.

### C. Trust Boundary
- **Boundary Definition**: In-process—tools run in the same memory space as the agent.
- **Implications**:
  - No network/protocol isolation: Tools can access shared state (e.g., global variables).
  - Shared memory: Tools can modify agent state or access sensitive data.
  - Shared permissions: Tools inherit the agent's OS/user permissions.
  - Larger local blast radius: A tool bug (e.g., infinite loop) can crash the entire agent process.

### D. Failure Model
- **Failure Points**:
  - Invalid schema: Pydantic model errors during registration.
  - Invalid arguments: ValidationError from Pydantic.
  - Tool runtime exception: Unhandled errors in handlers (e.g., division by zero).
  - Hallucinated tool usage: LLM selects non-existent tools or misuses existing ones.
- **Surfacing Failures**: Errors are caught in `execute_tool()`, logged, and returned as toolResult with status "error". The LLM receives this and can respond accordingly, informing the user.

### E. Security Implications
- **Focus on Direct Tools**:
  - Lack of isolation: Tools can execute arbitrary code if handlers are compromised.
  - Risk of arbitrary code execution: Malicious prompts could trick the LLM into selecting tools that expose data or run unsafe operations.
  - Prompt injection: User inputs could manipulate LLM to misuse tools (e.g., invalid inputs causing exceptions).
  - Data exposure: Shared memory risks leaking sensitive data between tools/agent.
- **Protections**: Input validation via Pydantic; logging for audit. Missing: No sandboxing, network isolation, or access controls.

### F. Operational and Governance Considerations
- **Deployment**: Tools are coupled with the agent—deployed as one unit.
- **Scalability**: Tools scale only with the agent process (e.g., via more instances).
- **Observability**: Local logs (via logging module); no distributed tracing.
- **Versioning**: Tools tied to agent releases—changes require redeployment.
- **Ownership**: Tool changes need code review and agent redeployment.
- **Testing Strategy**:
  - Unit tests: Validate tool handlers and schemas.
  - Integration tests: Test agent + tools end-to-end.
  - Negative/security tests: Invalid inputs, exceptions, prompt injection attempts.

### G. Suitability
- **Appropriate When**: Prototyping, low-risk environments, or when tools are simple and trusted (e.g., internal calculators).
- **Avoid When**: High-security needs, untrusted tools, or scaling requirements—use remote patterns for isolation.

## Proof Summary
- **Proven**:
  - Direct tool execution works: Handlers run in-process, returning results to the LLM.
  - Tool contract enforced locally: Pydantic validates inputs before execution.
  - Execution is in-process: No network calls; shared memory boundary.
- **Not Proven**:
  - Protocol boundary: No serialization or remote invocation.
  - Remote execution: Tools are local, not distributed.
  - Independent tool scaling: Tools can't scale separately from the agent.
  - Strong isolation: No sandboxing or permission boundaries.