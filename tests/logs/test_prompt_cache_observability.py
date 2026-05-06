import pytest

from aworld.agents.llm_agent import Agent
from aworld.config.conf import AgentConfig
from aworld.core.context.base import Context
from aworld.core.context.session import Session
from aworld.core.task import Task
from aworld.logs import prompt_log
from aworld.logs.prompt_log import PromptLogger
from aworld.models.utils import normalize_usage
from aworld.runners.event_runner import TaskEventRunner


def _build_agent(name: str = "Aworld") -> Agent:
    return Agent(
        name=name,
        conf=AgentConfig(
            llm_provider="openai",
            llm_model_name="fake-model",
            llm_api_key="fake-key",
        ),
    )


def _build_context(task_id: str = "task-1") -> Context:
    context = Context(task_id=task_id)
    context.session = Session(session_id="session-1")
    context.set_task(Task(id=task_id, name="test-task"))
    return context


def test_normalize_usage_maps_prompt_cache_fields():
    normalized = normalize_usage(
        {
            "prompt_tokens": 100,
            "completion_tokens": 20,
            "total_tokens": 120,
            "cache_read_input_tokens": 80,
            "cache_creation_input_tokens": 40,
        }
    )

    assert normalized["prompt_tokens"] == 100
    assert normalized["completion_tokens"] == 20
    assert normalized["total_tokens"] == 120
    assert normalized["cache_hit_tokens"] == 80
    assert normalized["cache_write_tokens"] == 40
    assert "cache_read_input_tokens" not in normalized
    assert "cache_creation_input_tokens" not in normalized


def test_prompt_logger_logs_prompt_cache_observability(monkeypatch):
    agent = _build_agent()
    context = _build_context()
    context.context_info["prompt_cache_observability"] = {
        "assembly_provider": "LegacyMessageAssembly",
        "provider_name": "openai",
        "cache_aware_assembly": False,
        "provider_native_cache": True,
        "stable_prefix_hash": "stable-hash-123",
    }
    context.context_info["llm_calls"] = [
        {
            "call_id": "call-1",
            "usage": {
                "prompt_tokens": 100,
                "completion_tokens": 20,
                "total_tokens": 120,
                "cache_hit_tokens": 80,
                "cache_write_tokens": 40,
            },
        }
    ]

    lines = []
    monkeypatch.setattr(prompt_log.prompt_logger, "info", lines.append)

    PromptLogger.log_prompt_cache_observability(agent, context)

    joined = "\n".join(lines)
    assert "PROMPT CACHE OBSERVABILITY" in joined
    assert "LegacyMessageAssembly" in joined
    assert "openai" in joined
    assert "stable-hash-123" in joined
    assert "80" in joined
    assert "40" in joined


def test_task_event_runner_finished_message_includes_cache_usage():
    message = TaskEventRunner._format_task_finished_message(
        task_id="task-1",
        is_sub_task=False,
        time_cost=1.25,
        token_usage={
            "prompt_tokens": 100,
            "completion_tokens": 20,
            "total_tokens": 120,
            "cache_hit_tokens": 80,
            "cache_write_tokens": 40,
        },
    )

    assert "main task task-1 finished" in message
    assert "cache_hit_tokens" in message
    assert "cache_write_tokens" in message
