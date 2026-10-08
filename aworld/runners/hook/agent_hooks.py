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
    desc="Intercept repeated read-only work after a delivery convergence gate arms",
)
class MutationGatePreToolHook(PreToolCallHook):
    """Keep pre- and post-candidate convergence above the provider boundary."""

    async def exec(self, message: Message, context: Context = None) -> Message | None:
        from aworld.runners.execution_protocol import mutation_gate_interception

        actions = message.payload if isinstance(message.payload, list) else []
        gate_receipt = mutation_gate_interception(context, actions)
        if gate_receipt is None:
            return None
        count = int(gate_receipt.get("consecutive_read_only_observations", 0) or 0)
        post_candidate = gate_receipt.get("kind") == "candidate_convergence_required"
        if post_candidate:
            count = int(
                gate_receipt.get(
                    "post_candidate_no_delivery_progress_observations",
                    gate_receipt.get("post_candidate_read_only_observations", 0),
                )
                or 0
            )
            message_text = (
                "This Tool call is outside the active candidate convergence "
                "admission. Each current candidate permits up to three bounded "
                "mechanically read-only diagnostic calls, one per Tool batch, "
                "without verifier or repair authorization. Registered validation "
                "does not spend that diagnostic allowance. Once it is exhausted, "
                "make one bounded "
                "revision targeting only declared deliverables with an exact "
                "Tool-argument signature that is new for the current candidate, "
                "use one evidence-backed repair, or submit the current result "
                "accurately without Tools. Each "
                "candidate-bound declared-revision signature is admitted once. A "
                "mixed batch does not widen admission: repeated revisions, "
                "additional declared revisions, helper or unrelated mutations, "
                "and unknown mutations remain blocked. Use a direct file write "
                "or an exact-file copy primitive for named file outputs; "
                "directory-ambiguous and recursive writers remain blocked."
            )
        else:
            message_text = (
                "This Tool call is outside the active candidate-production "
                "admission. Create or modify the exact declared deliverable, or "
                "execute the exact model-bound contractless candidate action."
            )
        error_code = str(gate_receipt["kind"])
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
                    "block_all": gate_receipt.get("block_all") is True,
                    "error_code": error_code,
                    "content_type": error_code,
                    "message": message_text,
                    "source_receipt": gate_receipt,
                },
                "additional_context": (
                    "AWorld convergence admission intercepted part of a Tool "
                    f"batch after {count} observations without delivery progress. "
                    + (
                        "Use at most three candidate-bound mechanically read-only "
                        "diagnostic calls, one per batch, without verifier or repair "
                        "authorization; registered validation does not spend the "
                        "diagnostic allowance. Once exhausted, make a declared "
                        "revision, use an evidence-backed repair, or submit. "
                        "Repeated, mixed, helper, unrelated, and unknown mutations "
                        "stay blocked. Named file outputs require a direct write or "
                        "exact-file copy primitive."
                        if post_candidate
                        else "Create or modify the smallest relevant candidate now."
                    )
                ),
            },
        )
