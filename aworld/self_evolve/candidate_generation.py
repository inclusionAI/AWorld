from __future__ import annotations

import uuid
from typing import Any

from aworld.agents.prompt_budgeted_agent import PromptBudgetedAgent
from aworld.config.conf import AgentConfig, ModelConfig
from aworld.core.agent.swarm import Swarm
from aworld.core.common import TaskStatusValue
from aworld.core.context.amni.local import LocalIsolatedApplicationContext
from aworld.core.context.amni.prompt.assembly.budget import PromptBudgetPolicy
from aworld.core.context.base import Context
from aworld.core.task import Task
from aworld.logs.util import logger
from aworld.runner import Runners


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


class CandidateGenerationAgent(PromptBudgetedAgent):
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
        configured_max_tokens = _positive_token_limit(
            (model_config.params or {}).get("max_tokens")
        )
        configured_max_completion_tokens = _positive_token_limit(
            (model_config.params or {}).get("max_completion_tokens")
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
        super().__init__(
            name="self-evolve-candidate-generator",
            conf=AgentConfig(
                llm_config=model_config.model_copy(deep=True),
                max_steps=1,
            ),
            prompt_budget_policy=PromptBudgetPolicy(
                reserved_output_tokens=self.output_token_limit,
            ),
            prompt_budget_section_hints=[
                {
                    "name": "candidate_output_contract",
                    "required": True,
                    "compressible": False,
                },
                {
                    "name": "current_task",
                    "required": True,
                    "compressible": False,
                },
            ],
            system_prompt=(
                "You generate one self-evolve candidate package. Return only a JSON "
                "object matching the candidate_output_contract in the task. Do not wrap "
                "the JSON in prose. Keep domain-specific replay behavior inside "
                "candidate-owned files and do not invent unavailable recordings."
            ),
            tool_names=[],
            llm_max_attempts=1,
        )
        self._task_failures: dict[str, dict[str, str]] = {}
        self.conf.llm_config.llm_stream_call = False
        Swarm.register_agent([self])

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
        try:
            return await super().invoke_model(messages or [], message=message, **kwargs)
        except CandidateGenerationInfrastructureError as exc:
            self._record_infrastructure_failure(message, exc)
            raise
        except Exception as exc:
            typed_failure = _find_infrastructure_failure(exc)
            failure = (
                CandidateGenerationInfrastructureError(
                    stage=typed_failure.stage,
                    error_type=typed_failure.error_type,
                )
                if typed_failure is not None
                else CandidateGenerationInfrastructureError(
                    stage="agent_runtime",
                    error_type=type(exc).__name__,
                )
            )
            self._record_infrastructure_failure(message, failure)
            raise failure from None

    def _record_infrastructure_failure(
        self,
        message: Any,
        failure: CandidateGenerationInfrastructureError,
    ) -> None:
        context = getattr(message, "context", None)
        context_info = getattr(context, "context_info", None)
        if context_info is not None:
            context_info["candidate_generation_failure"] = failure.to_diagnostic()
        task_id = getattr(context, "task_id", None)
        if isinstance(task_id, str) and task_id:
            self._task_failures[task_id] = failure.to_diagnostic()

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
        task = self.build_task(prompt)
        try:
            responses = await Runners.run_task(task)
        except CandidateGenerationInfrastructureError as exc:
            recorded_failure = self._task_failures.pop(task.id, None)
            if isinstance(recorded_failure, dict):
                raise CandidateGenerationInfrastructureError(
                    stage=str(recorded_failure.get("stage") or exc.stage),
                    error_type=str(
                        recorded_failure.get("error_type") or exc.error_type
                    ),
                ) from None
            raise
        except Exception as exc:
            recorded_failure = self._task_failures.pop(task.id, None)
            if isinstance(recorded_failure, dict):
                raise CandidateGenerationInfrastructureError(
                    stage=str(recorded_failure.get("stage") or "agent_runtime"),
                    error_type=str(
                        recorded_failure.get("error_type") or type(exc).__name__
                    ),
                ) from None
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

        response = responses.get(task.id) if isinstance(responses, dict) else None
        return self.candidate_response_from_task(task, response)

    def build_task(self, prompt: str, *, task_id: str | None = None) -> Task:
        task_id = task_id or f"self-evolve-candidate-{uuid.uuid4().hex}"
        context = LocalIsolatedApplicationContext.create(
            task_id=task_id,
            session_id=task_id,
            task_content=prompt,
        )
        return Task(
            id=task_id,
            session_id=task_id,
            input=prompt,
            agent=self,
            context=context,
            runner_cls="aworld.self_evolve.runtime.SelfEvolveCandidateTaskRunner",
        )

    def pop_task_failure(
        self,
        task: Task,
    ) -> CandidateGenerationInfrastructureError | None:
        recorded_failure = self._task_failures.pop(task.id, None)
        if recorded_failure is None:
            recorded_failure = task.context.context_info.get(
                "candidate_generation_failure"
            )
        if isinstance(recorded_failure, dict):
            return CandidateGenerationInfrastructureError(
                stage=str(recorded_failure.get("stage") or "agent_runtime"),
                error_type=str(recorded_failure.get("error_type") or "RuntimeError"),
            )
        return None

    def candidate_response_from_task(
        self,
        task: Task,
        response: Any,
    ) -> str:
        failure = self.pop_task_failure(task)
        if failure is not None:
            raise failure
        if response is None:
            raise CandidateGenerationInfrastructureError(
                stage="task_runner",
                error_type="MissingTaskResponse",
            )
        if not response.success or response.status not in {
            TaskStatusValue.SUCCESS,
            "finished",
        }:
            raise CandidateGenerationInfrastructureError(
                stage="task_runner",
                error_type="CandidateTaskFailed",
            )
        content = response.answer
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
