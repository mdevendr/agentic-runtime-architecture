from __future__ import annotations

from dataclasses import asdict, dataclass
from uuid import uuid4


@dataclass(frozen=True)
class ExecutionContext:
    """Correlation envelope carried across Runtime, MCP, Gateway, and targets."""

    session_id: str
    message_id: str
    correlation_id: str
    trace_id: str | None = None

    @classmethod
    def new(
        cls,
        session_id: str | None = None,
        message_id: str | None = None,
        trace_id: str | None = None,
    ) -> "ExecutionContext":
        session = session_id or f"session-{uuid4()}"
        return cls(
            session_id=session,
            message_id=message_id or f"message-{uuid4()}",
            correlation_id=f"corr-{uuid4()}",
            trace_id=trace_id,
        )

    def for_tool(self, tool_use_id: str, tool_name: str) -> dict[str, str]:
        values = {
            **asdict(self),
            "tool_use_id": tool_use_id,
            "tool_name": tool_name,
        }
        return {key: value for key, value in values.items() if value is not None}


def extract_trace_id(headers: dict[str, str] | None) -> str | None:
    """Accept either W3C traceparent or AWS X-Ray trace header."""

    if not headers:
        return None

    return (
        headers.get("traceparent")
        or headers.get("Traceparent")
        or headers.get("x-amzn-trace-id")
        or headers.get("X-Amzn-Trace-Id")
    )

