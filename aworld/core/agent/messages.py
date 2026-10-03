"""Minimal model boundary: text, assistant tool calls and tool results."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from typing import Mapping, Protocol

from aworld.core.tool.function import ToolSchema
from .usage import TokenUsage


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: Mapping[str, object]

    def __post_init__(self) -> None:
        if any(not isinstance(value, str) or not value.strip() for value in (self.id, self.name)):
            raise ValueError("Tool call id and name must be non-empty")
        object.__setattr__(self, "arguments", deepcopy(dict(self.arguments)))


@dataclass(frozen=True)
class UserMessage:
    content: str
    role: str = field(default="user", init=False)


@dataclass(frozen=True)
class AssistantMessage:
    content: str = ""
    tool_calls: tuple[ToolCall, ...] = ()
    usage: TokenUsage | None = None
    role: str = field(default="assistant", init=False)

    def __post_init__(self) -> None:
        if not isinstance(self.content, str):
            raise TypeError("Assistant content must be text")
        if self.usage is not None and not isinstance(self.usage, TokenUsage):
            raise TypeError("usage must be TokenUsage or None")
        calls = tuple(self.tool_calls)
        if not all(isinstance(call, ToolCall) for call in calls):
            raise TypeError("tool_calls must contain ToolCall values")
        object.__setattr__(self, "tool_calls", deepcopy(calls))


@dataclass(frozen=True)
class ToolResultMessage:
    tool_call_id: str
    name: str
    content: object
    is_error: bool = False
    role: str = field(default="tool", init=False)


@dataclass(frozen=True)
class ModelRequest:
    system_prompt: str
    messages: tuple[UserMessage | AssistantMessage | ToolResultMessage, ...]
    tools: tuple[ToolSchema, ...]
    max_output_tokens: int | None = None

    def __post_init__(self):
        if self.max_output_tokens is not None and (type(self.max_output_tokens) is not int or self.max_output_tokens <= 0):
            raise ValueError("max_output_tokens must be a positive integer or None")


class Model(Protocol):
    async def complete(self, request: ModelRequest) -> AssistantMessage: ...


def messages_from_history(entries):
    """Project facts into model messages; leave incomplete tool turns in history.

    A cancelled run may retain a declared call with no result. Omit that turn
    from the request view instead of deleting history or fabricating a result.
    """
    messages = []
    for entry in entries:
        if entry.kind == "input":
            if not isinstance(entry.data, str):
                raise TypeError("Agent history input must be text")
            messages.append(UserMessage(entry.data))
        elif entry.kind == "summary":
            messages.append(UserMessage("<context-summary>\n" + entry.data["content"] + "\n</context-summary>"))
        elif entry.kind == "assistant":
            data = entry.data
            messages.append(AssistantMessage(data["content"], tuple(
                ToolCall(call["id"], call["name"], call["arguments"]) for call in data["tool_calls"]
            )))
        elif entry.kind == "tool.result" and isinstance(entry.data, dict) and "tool_call_id" in entry.data:
            data = entry.data
            messages.append(ToolResultMessage(data["tool_call_id"], data["name"], data["content"], data["is_error"]))
    view = []
    i = 0
    while i < len(messages):
        message = messages[i]
        i += 1
        if isinstance(message, AssistantMessage) and message.tool_calls:
            results = []
            while i < len(messages) and isinstance(messages[i], ToolResultMessage):
                results.append(messages[i])
                i += 1
            expected = {call.id: call.name for call in message.tool_calls}
            actual = {result.tool_call_id: result.name for result in results}
            if expected == actual and len(results) == len(message.tool_calls):
                view.extend([message, *results])
        elif not isinstance(message, ToolResultMessage):
            view.append(message)
    return tuple(view)
