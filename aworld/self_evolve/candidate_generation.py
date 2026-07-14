from __future__ import annotations

import uuid
from typing import Any

from aworld.agents.llm_agent import Agent
from aworld.config.conf import AgentConfig, ModelConfig
from aworld.core.context.base import Context
from aworld.core.context.session import Session
from aworld.core.event.base import Message
from aworld.core.task import Task
from aworld.logs.util import logger


DEFAULT_CANDIDATE_OUTPUT_TOKEN_LIMIT = 8192


class CandidateGenerationInfrastructureError(RuntimeError):
    """Typed terminal failure for one candidate-generation population."""

    code = "candidate_generation_infrastructure_error"

    def __init__(self, *, stage: str, error_type: str) -> None:
        self.stage = stage
        self.error_type = error_type
        super().__init__(
            f"candidate generation infrastructure failed at {stage} ({error_type})"
        )

    def to_diagnostic(self) -> dict[str, str]:
        return {
            "code": self.code,
            "stage": self.stage,
            "error_type": self.error_type,
        }


class _SanitizingProvider:
    """Convert provider exceptions before generic Agent diagnostics can persist them."""

    def __init__(self, delegate: Any) -> None:
        self._delegate = delegate

    def __getattr__(self, name: str) -> Any:
        return getattr(self._delegate, name)

    async def acompletion(self, **kwargs: Any) -> Any:
        try:
            return await self._delegate.acompletion(**kwargs)
        except CandidateGenerationInfrastructureError:
            raise
        except Exception as exc:
            raise CandidateGenerationInfrastructureError(
                stage="model_provider",
                error_type=type(exc).__name__,
            ) from None


class CandidateGenerationAgent(Agent):
    """AWorld agent used for one bounded self-evolve candidate generation call."""

    def __init__(
        self,
        *,
        model_config: ModelConfig,
        output_token_limit: int = DEFAULT_CANDIDATE_OUTPUT_TOKEN_LIMIT,
    ) -> None:
        if isinstance(output_token_limit, bool) or output_token_limit <= 0:
            raise ValueError("output_token_limit must be positive")
        framework_output_limit = int(output_token_limit)
        super().__init__(
            name="self-evolve-candidate-generator",
            conf=AgentConfig(
                llm_config=model_config.model_copy(deep=True),
                max_steps=1,
            ),
            system_prompt=(
                "You generate one self-evolve candidate package. Return only a JSON "
                "object matching the candidate_output_contract in the task. Do not wrap "
                "the JSON in prose. Keep domain-specific replay behavior inside "
                "candidate-owned files and do not invent unavailable recordings."
            ),
            tool_names=[],
            llm_max_attempts=1,
        )
        model_params = dict(self.conf.llm_config.params or {})
        configured_max_tokens = _positive_token_limit(
            model_params.pop("max_tokens", None)
        )
        configured_max_completion_tokens = _positive_token_limit(
            model_params.pop("max_completion_tokens", None)
        )
        configured_limits = [
            item
            for item in (
                configured_max_tokens,
                configured_max_completion_tokens,
            )
            if item is not None
        ]
        self.output_token_limit = min(
            [framework_output_limit, *configured_limits]
        )
        self.output_token_parameter = (
            "max_completion_tokens"
            if configured_max_completion_tokens is not None
            else "max_tokens"
        )
        self.conf.llm_config.params = model_params
        self.conf.llm_config.llm_stream_call = False

    @property
    def llm(self) -> Any:
        model = super().llm
        if not isinstance(model.provider, _SanitizingProvider):
            model.provider = _SanitizingProvider(model.provider)
        return model

    async def invoke_model(
        self,
        messages: list[dict[str, Any]] | None = None,
        message: Any = None,
        **kwargs: Any,
    ) -> Any:
        requested_limits = [
            item
            for item in (
                _positive_token_limit(kwargs.pop("max_tokens", None)),
                _positive_token_limit(kwargs.pop("max_completion_tokens", None)),
            )
            if item is not None
        ]
        kwargs[self.output_token_parameter] = min(
            [self.output_token_limit, *requested_limits]
        )
        return await super().invoke_model(messages or [], message=message, **kwargs)

    async def _save_failed_request_context(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        error: str,
        attempt: int,
        context: Context,
    ) -> None:
        # The self-evolve report already records a bounded typed diagnostic. Avoid
        # creating a second persistence surface containing trajectory evidence.
        return None

    def _log_messages(
        self,
        messages: list[dict[str, Any]],
        context: Context,
        **kwargs: Any,
    ) -> None:
        prompt_chars = sum(len(str(item.get("content", ""))) for item in messages)
        logger.info(
            "self_evolve.candidate_generation.request "
            f"message_count={len(messages)} prompt_chars={prompt_chars}"
        )

    async def generate(self, prompt: str) -> str:
        invocation_id = uuid.uuid4().hex
        context = Context(
            task_id=f"self-evolve-candidate-{invocation_id}",
            session=Session(session_id=f"self-evolve-candidate-{invocation_id}"),
        )
        context.set_task(Task(id=context.task_id, input=prompt, context=context))
        context.agent_info.current_agent_id = self.id()
        message = Message(headers={"context": context})
        messages = [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": prompt},
        ]
        _, assembled_messages, _ = self._build_prompt_assembly_state(
            context=context,
            messages=messages,
            tools=None,
            request_kwargs={
                self.output_token_parameter: self.output_token_limit,
            },
        )
        try:
            response = await self.invoke_model(
                assembled_messages,
                message=message,
                stream=False,
            )
        except CandidateGenerationInfrastructureError:
            raise
        except Exception as exc:
            typed_failure = _find_infrastructure_failure(exc)
            if typed_failure is not None:
                raise CandidateGenerationInfrastructureError(
                    stage=typed_failure.stage,
                    error_type=typed_failure.error_type,
                ) from None
            raise CandidateGenerationInfrastructureError(
                stage="agent_runtime",
                error_type=type(exc).__name__,
            ) from None

        content = getattr(response, "content", None)
        if not isinstance(content, str) or not content.strip():
            raise CandidateGenerationInfrastructureError(
                stage="agent_response",
                error_type="EmptyCandidateResponse",
            )
        return content


def _find_infrastructure_failure(
    exc: BaseException,
) -> CandidateGenerationInfrastructureError | None:
    current: BaseException | None = exc
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, CandidateGenerationInfrastructureError):
            return current
        current = current.__cause__ or current.__context__
    return None


def _positive_token_limit(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        return None
    return value
