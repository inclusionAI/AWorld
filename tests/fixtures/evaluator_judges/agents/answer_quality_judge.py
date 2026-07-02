from __future__ import annotations

import os
from pathlib import Path

from aworld.agents.llm_agent import Agent
from aworld.config import AgentConfig, ModelConfig
from aworld.core.agent.swarm import Swarm
from aworld_cli.core import agent


PROMPT_PATH = Path(__file__).resolve().parents[1] / "answer_quality_agent.md"


def _prompt() -> str:
    return PROMPT_PATH.read_text(encoding="utf-8")


@agent(
    name="answer-quality-judge",
    desc="Answer-quality judge fixture for evaluator source tests.",
    metadata={"source": "tests.fixture", "prompt_path": str(PROMPT_PATH)},
    unique=True,
)
def build_answer_quality_judge_swarm() -> Swarm:
    judge = Agent(
        name="answer-quality-judge",
        desc="Evaluates task answers and returns the evaluator JSON schema.",
        conf=AgentConfig(
            llm_config=ModelConfig(
                llm_model_name=os.environ.get("LLM_MODEL_NAME", "gpt-4"),
                llm_provider=os.environ.get("LLM_PROVIDER", "openai"),
                llm_api_key=os.environ.get("LLM_API_KEY") or os.environ.get("OPENAI_API_KEY"),
                llm_base_url=os.environ.get("LLM_BASE_URL", "https://api.openai.com/v1"),
                llm_temperature=float(os.environ.get("LLM_TEMPERATURE", "0.1")),
                params={"max_completion_tokens": 4096},
            ),
            skill_configs={},
        ),
        system_prompt=_prompt(),
    )
    return Swarm(judge, max_steps=1, name="answer-quality-judge-swarm")
