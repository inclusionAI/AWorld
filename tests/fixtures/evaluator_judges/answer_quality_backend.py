from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from aworld.evaluations.substrate import AgentJudgeBackend


PROMPT_PATH = Path(__file__).resolve().parent / "answer_quality_agent.md"


def _prompt() -> str:
    return PROMPT_PATH.read_text(encoding="utf-8")


async def _deterministic_executor(prompt: Any, system_prompt: str) -> str:
    payload = {
        "task_id": "fixture",
        "score": 88,
        "verdict": "Pass",
        "veto_triggered": False,
        "Q1_correctness": 4,
        "Q2_completeness": 4,
        "Q3_relevance": 5,
        "Q4_clarity": 4,
        "Q5_faithfulness": 5,
        "dimensions": {
            "Q1_correctness": {
                "score": 4,
                "weight": 0.30,
                "evidence": ["fixture answer"],
                "rationale": "The fixture backend is deterministic for tests.",
            }
        },
        "errors": [],
        "top_strengths": ["Deterministic fixture response"],
        "top_improvements": [],
        "notes": "This backend-ref fixture verifies loading and schema plumbing without calling an LLM.",
    }
    return json.dumps(payload, ensure_ascii=False)


def _prompt_builder(case_input: dict[str, Any], target: dict[str, Any], suite: Any) -> str:
    return json.dumps(
        {
            "case": case_input,
            "state": {"answer": target.get("answer")},
            "required_output_schema": {"score": "number 0-100", "verdict": "string"},
            "instruction": "Evaluate the existing answer/state and return exactly one JSON object.",
        },
        ensure_ascii=False,
    )


def build_backend() -> AgentJudgeBackend:
    return AgentJudgeBackend(
        backend_id="answer-quality-fixture-backend",
        system_prompt=_prompt(),
        executor=_deterministic_executor,
        prompt_builder=_prompt_builder,
    )
