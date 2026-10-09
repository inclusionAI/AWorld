# coding: utf-8
# Copyright (c) 2025 inclusionAI.
import abc

from aworld.core.context.base import Context
from aworld.core.event.base import Message
from aworld.runners.hook.hook_factory import HookFactory
from aworld.runners.hook.hooks import PostLLMCallHook, PreLLMCallHook, PreToolCallHook
from aworld.utils.common import convert_to_snake


_MUTATION_GATE_REASON_GUIDANCE = {
    "effect_unknown": (
        "The call effect could not be mechanically proved. Replace shell "
        "pipelines, command chaining, and dynamic expansion with one direct "
        "bounded operation whose complete write set is visible. For inline or "
        "heredoc Python that imports modules, `python3 -I` is necessary to "
        "prevent workspace import shadowing, but it is not sufficient: every "
        "write must still be mechanically proved and confined to the admitted "
        "deliverable scope."
    ),
    "diagnostic_quota_exhausted": (
        "The bounded candidate diagnostic allowance is exhausted. Execute an "
        "exact registered validation, make one eligible new declared revision, "
        "use an evidence-backed repair, or submit without more Tools."
    ),
    "diagnostic_batch_limit": (
        "Only one unregistered mechanically read-only diagnostic is admitted "
        "per Tool batch. Issue any remaining eligible diagnostic in a later "
        "batch."
    ),
    "undeclared_helper": (
        "The mutation is not confined to declared deliverables. Remove helper "
        "or unrelated writes and use one operation whose complete target set is "
        "a subset of the declared deliverable set."
    ),
    "replayed_revision": (
        "That exact revision signature was already admitted for the current "
        "candidate. Use a materially different bounded declared revision, an "
        "exact registered validation, or submit."
    ),
    "validation_unregistered": (
        "This is not an exact framework-registered validation and is not "
        "currently eligible as a bounded diagnostic. Use the registered "
        "command with its registered working directory, or choose an admitted "
        "revision or submission."
    ),
    "revision_batch_limit": (
        "Only one declared revision is admitted per Tool batch. Put a distinct "
        "eligible revision in a later batch after observing the first result."
    ),
    "repair_unauthorized": (
        "No candidate-bound repair authorization admits this mutation. Obtain "
        "fresh typed failure evidence, modify only declared deliverables, or "
        "submit the current result."
    ),
    "candidate_plan_mismatch": (
        "The mutation does not match the exact model-bound candidate action. "
        "Use the bound capability, effect, and target shape, or publish a new "
        "typed plan before retrying."
    ),
    "call_identity_invalid": (
        "Every Tool call in the batch must have a unique non-empty call id. "
        "Reissue a valid batch before any call can execute."
    ),
    "finalization_latched": (
        "Tool-free finalization is already latched for this candidate. Submit "
        "the result accurately without another Tool call."
    ),
    "scope_ambiguous": (
        "The batch cannot be bound to exactly one active agent convergence "
        "scope. Reissue calls under one explicit agent scope."
    ),
}


def _mutation_gate_reason_guidance(receipt: dict) -> tuple[tuple[str, ...], str]:
    """Project only finite rejection codes into deterministic model guidance."""

    reasons: list[str] = []
    call_rejections = receipt.get("call_rejections")
    if isinstance(call_rejections, list):
        for value in call_rejections:
            if not isinstance(value, dict):
                continue
            reason = value.get("reason")
            if reason in _MUTATION_GATE_REASON_GUIDANCE and reason not in reasons:
                reasons.append(reason)
    guidance = " ".join(_MUTATION_GATE_REASON_GUIDANCE[value] for value in reasons)
    return tuple(reasons), guidance


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
        from aworld.core.tool_action_journal import tool_action_request_fingerprint

        actions = message.payload if isinstance(message.payload, list) else []
        gate_receipt = mutation_gate_interception(context, actions)
        if gate_receipt is None:
            return None
        rejection_reasons, reason_guidance = _mutation_gate_reason_guidance(
            gate_receipt
        )
        reason_summary = ", ".join(rejection_reasons) or "admission_mismatch"
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
                f"admission (reason: {reason_summary}). Each current candidate "
                "permits up to three bounded "
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
                f"admission (reason: {reason_summary}). Create or modify the "
                "exact declared deliverable, or execute the exact model-bound "
                "contractless candidate action. Declaring write paths without "
                "code that actually writes those exact files is not candidate "
                "production. When required public deliverables are still "
                "absent, produce them now; a tool-free handoff is not task "
                "completion."
            )
        if reason_guidance:
            message_text += " " + reason_guidance
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
                    "action_request_fingerprint": (
                        tool_action_request_fingerprint(actions)
                    ),
                    "tool_call_ids": gate_receipt["tool_call_ids"],
                    "block_all": gate_receipt.get("block_all") is True,
                    "error_code": error_code,
                    "content_type": error_code,
                    "message": message_text,
                    "source_receipt": gate_receipt,
                },
                "additional_context": (
                    "AWorld convergence admission intercepted part of a Tool "
                    f"batch after {count} observations without delivery progress; "
                    f"bounded rejection reason(s): {reason_summary}. "
                    + reason_guidance
                ),
            },
        )
