"""One model/tool loop. Output events observe execution; they never dispatch it."""

from __future__ import annotations

import asyncio
from copy import deepcopy
from typing import Callable, Mapping, Sequence
from hashlib import sha256

from aworld.core.session.protocols import RunContext
from aworld.core.tool.function import Tool, ToolExecutionError
from aworld.core.tool.local import default_tools
from aworld.core.tool.registry import ToolRegistry
from .skill import Skill
from .messages import AssistantMessage, Model, ModelRequest
from .messages import messages_from_history as _messages


class AgentLoopLimitError(RuntimeError):
    pass

class Agent:
    """Configured execution capability; conversation and turn state stay in Context/Run."""

    def __init__(
        self, *, model: Model, tools: Sequence[Tool] | ToolRegistry | None = None,
        skills: Sequence[Skill] = (), system_prompt: str = "",
        max_turns: int = 20,
        runtime_prompt: Callable[[RunContext, int], str] | None = None,
        prompt_metadata: Mapping[str, object] | None = None,
    ) -> None:
        if not callable(getattr(model, "complete", None)):
            raise TypeError("model must implement async complete()")
        if isinstance(max_turns, bool) or not isinstance(max_turns, int) or max_turns < 1:
            raise ValueError("max_turns must be a positive integer")
        if not isinstance(system_prompt, str):
            raise TypeError("system_prompt must be text")
        skills = tuple(skills)
        if not all(isinstance(skill, Skill) for skill in skills):
            raise TypeError("skills must contain Skill values")
        if len({skill.name for skill in skills}) != len(skills):
            raise ValueError("Skill names must be unique")
        registry = tools if isinstance(tools, ToolRegistry) else ToolRegistry(default_tools() if tools is None else tools)
        registry = registry.extend(tool for skill in skills for tool in skill.tools)
        contributions = [system_prompt]
        for skill in skills:
            contributions.append(f"Skill {skill.name}: {skill.description}")
            if skill.location:
                registry.get("read")
                contributions.append(f"Read {skill.location} when this skill applies; relative references are based on its directory.")
            if skill.instructions:
                contributions.append(skill.instructions)
        self._model = model
        self._tools = registry
        self._system_prompt = "\n\n".join(part for part in contributions if part)
        self._skills = skills
        self._max_turns = max_turns
        self._runtime_prompt = runtime_prompt
        self._prompt_metadata = deepcopy(dict(prompt_metadata)) if prompt_metadata is not None else None

    @property
    def tools(self) -> ToolRegistry:
        return self._tools

    @property
    def skills(self) -> tuple[Skill, ...]:
        return self._skills

    def validate_input(self, input: object) -> None:
        if not isinstance(input, str) or not input.strip():
            raise ValueError("Agent input must be non-empty text")

    async def run(self, input: object, context: RunContext) -> str:
        self.validate_input(input)
        seen_calls = context.resource((self, "tool_call_ids"), set)
        for turn in range(1, self._max_turns + 1):
            # Prepare again after every tool batch; no cached second message history.
            history = await context.get_context()
            for entry in history:
                if entry.kind == "assistant":
                    seen_calls.update(call["id"] for call in entry.data["tool_calls"])
            system_prompt = self._system_prompt
            if self._runtime_prompt is not None:
                system_prompt += "\n\n" + self._runtime_prompt(context, turn)
            if self._prompt_metadata is not None:
                context.append("system", {**self._prompt_metadata, "content": system_prompt,
                    "sha256": sha256(system_prompt.encode()).hexdigest(), "turn": turn})
            request = ModelRequest(
                system_prompt, _messages(history),
                self._tools.schemas(),
            )
            request = await context.prepare_request(request, model=self._model)
            from aworld.core.context.budget import request_anchor
            anchor = request_anchor(request, self._model)
            context.emit("model.started", {"turn": turn})
            response = None
            try:
                response = await self._model.complete(deepcopy(request))
                if not isinstance(response, AssistantMessage):
                    raise TypeError("Model must return AssistantMessage")
                response = deepcopy(response)
                ids = [call.id for call in response.tool_calls]
                if len(set(ids)) != len(ids) or seen_calls.intersection(ids):
                    raise ValueError("Tool call IDs must be unique in context")
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                usage = getattr(exc, "usage", None) or getattr(response, "usage", None)
                context.append("model.error", {"turn": turn, "error_type": type(exc).__name__,
                                               "usage": usage.to_dict() if usage is not None else None})
                raise
            seen_calls.update(ids)
            context.append("assistant", {
                "content": response.content,
                "tool_calls": [{"id": call.id, "name": call.name, "arguments": dict(call.arguments)}
                               for call in response.tool_calls],
                "usage": response.usage.to_dict() if response.usage is not None else None,
                "context_anchor": anchor,
            })
            if response.content:
                context.set_output(response.content)
            context.emit("model.finished", {"turn": turn, "content": response.content, "tool_calls": len(ids),
                                           "usage": response.usage.to_dict() if response.usage is not None else None})
            if not response.tool_calls:
                return response.content
            for call in response.tool_calls:
                context.emit("tool.started", {"id": call.id, "name": call.name, "arguments": dict(call.arguments)})
                try:
                    content = await self._tools.execute(call.name, call.arguments, context)
                    is_error = False
                except asyncio.CancelledError:
                    raise
                except ToolExecutionError as exc:
                    content = exc.result
                    is_error = True
                except Exception as exc:
                    content = {"error": type(exc).__name__, "message": str(exc)}
                    is_error = True
                result = {"tool_call_id": call.id, "name": call.name, "content": content, "is_error": is_error}
                context.append("tool.result", result)
                context.emit("tool.finished", result)
        raise AgentLoopLimitError(f"max_turns_exceeded: {self._max_turns} model turns")
