from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class PendingToolCall:
    tool_use_id: str
    tool_name: str
    tool_input: dict[str, Any]
    status: str = "PENDING"
    retry_count: int = 0


@dataclass
class ConverseExecutionFrame:
    session_id: str
    message_id: str
    messages: list[dict[str, Any]]
    pending_tools: list[PendingToolCall] = field(default_factory=list)
    correlation_id: str | None = None

    def to_item(self) -> dict[str, Any]:
        item = asdict(self)
        item["pk"] = f"SESSION#{self.session_id}"
        item["sk"] = f"MESSAGE#{self.message_id}"
        return item


class InMemoryStateStore:
    """Local evidence store for resumable Converse execution frames."""

    def __init__(self) -> None:
        self._items: dict[tuple[str, str], ConverseExecutionFrame] = {}

    def put_frame(self, frame: ConverseExecutionFrame) -> None:
        self._items[(frame.session_id, frame.message_id)] = frame

    def get_frame(self, session_id: str, message_id: str) -> ConverseExecutionFrame:
        return self._items[(session_id, message_id)]

