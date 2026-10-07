# coding: utf-8
# Copyright (c) 2025 inclusionAI.
import abc

from aworld.core.context.base import Context
from aworld.core.event.base import Message
from aworld.runners.hook.hook_factory import HookFactory
from aworld.runners.hook.hooks import PostLLMCallHook, PreLLMCallHook, PreToolCallHook
from aworld.utils.common import convert_to_snake


@HookFactory.register(name="PreLLMCallContextProcessHook",
                      desc="PreLLMCallContextProcessHook")
class PreLLMCallContextProcessHook(PreLLMCallHook):
    """Process in the hook point of the pre_llm_call."""
    __metaclass__ = abc.ABCMeta

    def name(self):
        return convert_to_snake("PreLLMCallContextProcessHook")

    async def exec(self, message: Message, context: Context = None) -> Message:
        # and do something
        pass


@HookFactory.register(name="PostLLMCallContextProcessHook",
                      desc="PostLLMCallContextProcessHook")
class PostLLMCallContextProcessHook(PostLLMCallHook):
    """Process in the hook point of the post_llm_call."""
    __metaclass__ = abc.ABCMeta

    def name(self):
        return convert_to_snake("PostLLMCallContextProcessHook")

    async def exec(self, message: Message, context: Context = None) -> Message:
        # get context
        pass


@HookFactory.register(name="PostLLMTrajectoryHook",
                      desc="PostLLMTrajectoryHook")
class PostLLMTrajectoryHook(PostLLMCallHook):
    """Update trajectory after llm call."""
    def name(self):
        return convert_to_snake("PostLLMTrajectoryHook")
    async def exec(self, message: Message, context: Context = None) -> Message:
        # get context
        agent_message = message.headers.get("agent_message")
        if not agent_message:
            return None
        await context.update_task_trajectory(message=agent_message, task_id=context.task_id)


@HookFactory.register(
    name="MutationGatePreToolHook",
    desc="Intercept repeated read-only work after the candidate mutation gate arms",
)
class MutationGatePreToolHook(PreToolCallHook):
    """Keep convergence policy above the Sandbox provider boundary."""

    async def exec(self, message: Message, context: Context = None) -> Message | None:
        from aworld.runners.execution_protocol import mutation_gate_interception

        actions = message.payload if isinstance(message.payload, list) else []
        gate_receipt = mutation_gate_interception(context, actions)
        if gate_receipt is None:
            return None
        count = int(gate_receipt.get("consecutive_read_only_observations", 0) or 0)
        message_text = (
            "Further provably read-only work is gated. Create or modify the "
            "smallest relevant inspectable candidate now."
        )
        return Message(
            category="agent_hook",
            payload=None,
            sender="mutation_gate",
            session_id=getattr(context, "session_id", None),
            headers={
                "tool_interception": {
                    "schema_version": "aworld.tool-interception/v1",
                    "kind": "block",
                    "tool_call_ids": gate_receipt["tool_call_ids"],
                    "error_code": "candidate_mutation_required",
                    "content_type": "candidate_mutation_required",
                    "message": message_text,
                    "source_receipt": gate_receipt,
                },
                "additional_context": (
                    "AWorld mutation gate intercepted a provably read-only Tool "
                    f"batch after {count} consecutive read-only observations. "
                    "Create or modify the smallest relevant candidate now."
                ),
            },
        )
