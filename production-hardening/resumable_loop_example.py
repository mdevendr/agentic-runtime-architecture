from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from shared.execution_context import ExecutionContext
from shared.idempotency import idempotency_key
from shared.state_store import ConverseExecutionFrame, InMemoryStateStore, PendingToolCall


def persist_pending_tool_call() -> tuple[InMemoryStateStore, ExecutionContext, str]:
    """
    Minimal evidence for a resumable Converse loop.

    The baseline examples use a synchronous while stopReason == "tool_use" loop.
    This example shows the production shape: persist the frame, release compute,
    then rehydrate and append the matching toolResult later.
    """

    store = InMemoryStateStore()
    context = ExecutionContext.new()
    tool_use_id = "tooluse-123"
    tool_name = "issue_refund"
    tool_input = {"order_id": "ORD-1001", "amount": 12.5}

    frame = ConverseExecutionFrame(
        session_id=context.session_id,
        message_id=context.message_id,
        correlation_id=context.correlation_id,
        messages=[
            {"role": "user", "content": [{"text": "Issue a refund for ORD-1001."}]},
            {
                "role": "assistant",
                "content": [
                    {
                        "toolUse": {
                            "toolUseId": tool_use_id,
                            "name": tool_name,
                            "input": tool_input,
                        }
                    }
                ],
            },
        ],
        pending_tools=[
            PendingToolCall(
                tool_use_id=tool_use_id,
                tool_name=tool_name,
                tool_input=tool_input,
            )
        ],
    )
    store.put_frame(frame)
    return store, context, tool_use_id


def rehydrate_with_tool_result() -> list[dict]:
    store, context, tool_use_id = persist_pending_tool_call()
    frame = store.get_frame(context.session_id, context.message_id)
    key = idempotency_key(context.session_id, context.message_id, tool_use_id)

    frame.messages.append(
        {
            "role": "user",
            "content": [
                {
                    "toolResult": {
                        "toolUseId": tool_use_id,
                        "status": "success",
                        "content": [
                            {
                                "json": {
                                    "status": "REFUND_ISSUED",
                                    "idempotency_key": key,
                                    "correlation_id": context.correlation_id,
                                }
                            }
                        ],
                    }
                }
            ],
        }
    )
    return frame.messages


if __name__ == "__main__":
    for message in rehydrate_with_tool_result():
        print(message)
