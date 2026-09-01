from aworld.core.event.base import Message
from aworld.runners.hook.hook_factory import HookFactory
from aworld.runners.hook.hooks import PreLLMCallHook

from ..steering.observability import log_applied_steering_event
from ..steering.context_adapter import adapt_applied_steering
from aworld.core.context.compiler import ContextObservationSidecar


@HookFactory.register(name="SteeringBeforeLlmHook")
class SteeringBeforeLlmHook(PreLLMCallHook):
    async def exec(self, message: Message, context=None) -> Message:
        steering = getattr(context, "_aworld_cli_steering", None) if context is not None else None
        session_id = getattr(context, "session_id", None) if context is not None else None
        if steering is None or not session_id:
            return message

        payload = message.payload if isinstance(message.payload, dict) else {}
        if "messages" not in payload:
            return message

        messages = list(payload.get("messages") or [])
        drained = steering.drain_for_checkpoint(session_id)
        if not drained:
            return message

        log_applied_steering_event(
            workspace_path=getattr(context, "workspace_path", None),
            session_id=session_id,
            task_id=getattr(context, "task_id", None),
            steering_items=drained,
            checkpoint="before_llm_call",
        )

        updated_messages = list(messages)
        for item in drained:
            updated_messages.append({"role": "user", "content": item.text})

        publisher = getattr(context, "publish_context_observation", None)
        if callable(publisher):
            source_identity = (
                f"aworld-cli://steering/{session_id}/"
                f"task-{getattr(context, 'task_id', 'unknown')}"
            )
            publisher(
                ContextObservationSidecar.from_adapter_result(
                    owner="cli.applied_steering",
                    namespace=session_id,
                    source_identity=source_identity,
                    result=adapt_applied_steering(
                        drained,
                        source_identity=source_identity,
                        task_epoch=context.task_epoch,
                        session_id=session_id,
                        task_id=context.task_id,
                    ),
                )
            )

        message.headers["updated_input"] = {"messages": updated_messages}
        return message
