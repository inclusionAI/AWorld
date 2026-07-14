from __future__ import annotations

import pytest

import aworld.self_evolve.candidate_generation as candidate_generation_module
from aworld.agents.llm_agent import Agent
from aworld.config.conf import ModelConfig
from aworld.core.agent.base import AgentFactory
from aworld.core.context.base import Context
from aworld.models.model_response import ModelResponse
from aworld.self_evolve.candidate_generation import (
    CandidateGenerationAgent,
    CandidateGenerationInfrastructureError,
    _SanitizingProvider,
)


def test_candidate_generation_agent_registers_with_aworld_runtime() -> None:
    agent = CandidateGenerationAgent(
        model_config=ModelConfig(
            llm_provider="openai",
            llm_model_name="candidate-model",
            llm_api_key="test-key",
        )
    )

    assert AgentFactory.agent_instance(agent.id()) is agent


@pytest.mark.asyncio
async def test_candidate_generation_agent_enforces_output_token_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    async def fake_invoke_model(self, messages, message=None, **kwargs):
        captured.update(kwargs)
        return "model-response"

    monkeypatch.setattr(Agent, "invoke_model", fake_invoke_model)
    agent = CandidateGenerationAgent(
        model_config=ModelConfig(
            llm_provider="openai",
            llm_model_name="candidate-model",
            llm_api_key="test-key",
        ),
        output_token_limit=4096,
    )

    result = await agent.invoke_model(
        [{"role": "user", "content": "generate a candidate"}],
        message=object(),
    )

    assert result == "model-response"
    assert captured["max_tokens"] == 4096


def test_candidate_generation_agent_removes_conflicting_model_output_limits() -> None:
    model_config = ModelConfig(
        llm_provider="openai",
        llm_model_name="candidate-model",
        llm_api_key="test-key",
        params={
            "max_tokens": 99999,
            "max_completion_tokens": 99999,
            "top_p": 0.8,
        },
    )

    agent = CandidateGenerationAgent(
        model_config=model_config,
        output_token_limit=4096,
    )

    assert model_config.params["max_tokens"] == 99999
    assert model_config.params["max_completion_tokens"] == 99999
    assert agent.conf.llm_config.params == {"top_p": 0.8}
    assert agent.output_token_limit == 4096
    assert agent.output_token_parameter == "max_completion_tokens"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("parameter", "configured_limit", "expected_limit"),
    [
        ("max_tokens", 1024, 1024),
        ("max_completion_tokens", 2048, 2048),
    ],
)
async def test_candidate_generation_agent_respects_profile_output_limit_and_parameter(
    monkeypatch: pytest.MonkeyPatch,
    parameter: str,
    configured_limit: int,
    expected_limit: int,
) -> None:
    captured: dict[str, object] = {}

    async def fake_invoke_model(self, messages, message=None, **kwargs):
        captured.update(kwargs)
        return "model-response"

    monkeypatch.setattr(Agent, "invoke_model", fake_invoke_model)
    agent = CandidateGenerationAgent(
        model_config=ModelConfig(
            llm_provider="openai",
            llm_model_name="candidate-model",
            llm_api_key="test-key",
            params={parameter: configured_limit},
        ),
        output_token_limit=4096,
    )

    await agent.invoke_model(
        [{"role": "user", "content": "generate a candidate"}],
        message=object(),
    )

    assert agent.output_token_parameter == parameter
    assert agent.output_token_limit == expected_limit
    assert captured[parameter] == expected_limit
    other_parameter = (
        "max_completion_tokens" if parameter == "max_tokens" else "max_tokens"
    )
    assert other_parameter not in captured


@pytest.mark.asyncio
async def test_candidate_generation_uses_aworld_agent_and_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    async def fake_invoke_model(self, messages, message=None, **kwargs):
        captured["messages"] = messages
        captured["context"] = message.context
        captured["kwargs"] = kwargs
        return ModelResponse(
            id="candidate-response",
            model="candidate-model",
            content='{"content":"# Candidate"}',
        )

    monkeypatch.setattr(Agent, "invoke_model", fake_invoke_model)
    model_config = ModelConfig(
        llm_provider="openai",
        llm_model_name="candidate-model",
        llm_api_key="test-key",
    )

    output = await CandidateGenerationAgent(
        model_config=model_config,
        output_token_limit=2048,
    ).generate("candidate evidence")

    assert output == '{"content":"# Candidate"}'
    assert isinstance(captured["context"], Context)
    assert [item["role"] for item in captured["messages"]] == ["system", "user"]
    assert captured["kwargs"]["max_tokens"] == 2048


@pytest.mark.asyncio
async def test_candidate_generation_runs_through_agent_model_path() -> None:
    captured: dict[str, object] = {}

    class FakeModel:
        provider_name = "openai"
        provider = object()

        async def acompletion(self, **kwargs):
            captured.update(kwargs)
            return ModelResponse(
                id="candidate-response",
                model="candidate-model",
                content='{"content":"# Candidate"}',
            )

    agent = CandidateGenerationAgent(
        model_config=ModelConfig(
            llm_provider="openai",
            llm_model_name="candidate-model",
            llm_api_key="test-key",
        ),
        output_token_limit=3072,
    )
    agent._llm = FakeModel()

    output = await agent.generate("candidate evidence")

    assert output == '{"content":"# Candidate"}'
    assert captured["max_tokens"] == 3072
    assert isinstance(captured["context"], Context)


@pytest.mark.asyncio
async def test_candidate_generation_sanitizes_provider_failure_before_agent_logging(
) -> None:
    class FailingProvider:
        async def acompletion(self, **kwargs):
            raise RuntimeError("Authorization: Bearer should-not-leak")

    provider = _SanitizingProvider(FailingProvider())

    with pytest.raises(CandidateGenerationInfrastructureError) as exc_info:
        await provider.acompletion(messages=[])

    assert exc_info.value.error_type == "RuntimeError"
    assert "should-not-leak" not in str(exc_info.value)


@pytest.mark.asyncio
async def test_candidate_generation_preserves_safe_underlying_failure_type(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FailingProvider:
        async def acompletion(self, **kwargs):
            raise RuntimeError("Authorization: Bearer should-not-leak")

    class FakeModel:
        provider_name = "openai"
        provider = FailingProvider()

        async def acompletion(self, **kwargs):
            return await self.provider.acompletion(**kwargs)

    monkeypatch.chdir(tmp_path)
    agent = CandidateGenerationAgent(
        model_config=ModelConfig(
            llm_provider="openai",
            llm_model_name="candidate-model",
            llm_api_key="test-key",
        )
    )
    agent._llm = FakeModel()

    with pytest.raises(CandidateGenerationInfrastructureError) as exc_info:
        await agent.generate("private trajectory evidence")

    assert exc_info.value.error_type == "RuntimeError"
    assert "should-not-leak" not in str(exc_info.value)
    assert not (tmp_path / "failed_requests").exists()


@pytest.mark.asyncio
async def test_candidate_generation_converts_runtime_failure_to_typed_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_invoke_model(self, messages, message=None, **kwargs):
        raise RuntimeError("Authorization: Bearer should-not-leak")

    monkeypatch.setattr(Agent, "invoke_model", fake_invoke_model)

    with pytest.raises(CandidateGenerationInfrastructureError) as exc_info:
        await CandidateGenerationAgent(
            model_config=ModelConfig(
                llm_provider="openai",
                llm_model_name="candidate-model",
                llm_api_key="test-key",
            ),
        ).generate("candidate evidence")

    assert exc_info.value.error_type == "RuntimeError"
    assert "should-not-leak" not in str(exc_info.value)


@pytest.mark.asyncio
async def test_candidate_generation_preserves_typed_failure_stage_through_agent_wrap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_invoke_model(self, messages, message=None, **kwargs):
        try:
            raise CandidateGenerationInfrastructureError(
                stage="model_provider",
                error_type="LLMResponseError",
            )
        except CandidateGenerationInfrastructureError as exc:
            raise RuntimeError("safe framework wrapper") from exc

    monkeypatch.setattr(Agent, "invoke_model", fake_invoke_model)
    agent = CandidateGenerationAgent(
        model_config=ModelConfig(
            llm_provider="openai",
            llm_model_name="candidate-model",
            llm_api_key="test-key",
        )
    )

    with pytest.raises(CandidateGenerationInfrastructureError) as exc_info:
        await agent.generate("candidate evidence")

    assert exc_info.value.stage == "model_provider"
    assert exc_info.value.error_type == "LLMResponseError"


@pytest.mark.asyncio
async def test_candidate_generation_does_not_persist_generic_failed_request(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    agent = CandidateGenerationAgent(
        model_config=ModelConfig(
            llm_provider="openai",
            llm_model_name="candidate-model",
            llm_api_key="test-key",
        )
    )

    await agent._save_failed_request_context(
        messages=[{"role": "user", "content": "private trajectory evidence"}],
        tools=[],
        error="Authorization: Bearer should-not-leak",
        attempt=1,
        context=Context(task_id="candidate-task"),
    )

    assert not (tmp_path / "failed_requests").exists()


def test_candidate_generation_agent_logs_request_shape_without_prompt_content(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    logs: list[str] = []
    monkeypatch.setattr(
        candidate_generation_module.logger,
        "info",
        lambda message: logs.append(str(message)),
    )
    agent = CandidateGenerationAgent(
        model_config=ModelConfig(
            llm_provider="openai",
            llm_model_name="candidate-model",
            llm_api_key="test-key",
        )
    )

    agent._log_messages(
        [{"role": "user", "content": "Authorization: Bearer should-not-leak"}],
        Context(task_id="candidate-task"),
    )

    assert logs
    assert "should-not-leak" not in "\n".join(logs)
