from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ToolLoopState:
    consecutive_tool_calls: int = 0
    last_tool_name: str | None = None
    repeated_tool_calls: int = 0
    failure_counts: dict[str, int] = field(default_factory=dict)


class ToolLoopCircuitBreaker:
    """Bound recursive tool execution independently from model behavior."""

    def __init__(
        self,
        max_consecutive_tool_calls: int = 5,
        max_repeated_tool_calls: int = 3,
        max_failures_per_class: int = 2,
    ) -> None:
        self.max_consecutive_tool_calls = max_consecutive_tool_calls
        self.max_repeated_tool_calls = max_repeated_tool_calls
        self.max_failures_per_class = max_failures_per_class
        self._states: dict[str, ToolLoopState] = {}

    def before_tool_call(
        self,
        session_id: str,
        tool_name: str,
        error_class: str | None = None,
    ) -> None:
        state = self._states.setdefault(session_id, ToolLoopState())
        state.consecutive_tool_calls += 1

        if state.last_tool_name == tool_name:
            state.repeated_tool_calls += 1
        else:
            state.last_tool_name = tool_name
            state.repeated_tool_calls = 1

        if error_class:
            state.failure_counts[error_class] = state.failure_counts.get(error_class, 0) + 1

        if state.consecutive_tool_calls > self.max_consecutive_tool_calls:
            raise RuntimeError("tool loop circuit breaker opened: too many consecutive tool calls")

        if state.repeated_tool_calls > self.max_repeated_tool_calls:
            raise RuntimeError("tool loop circuit breaker opened: repeated tool invocation")

        if error_class and state.failure_counts[error_class] > self.max_failures_per_class:
            raise RuntimeError("tool loop circuit breaker opened: repeated failure class")

    def final_response_emitted(self, session_id: str) -> None:
        self._states.pop(session_id, None)

