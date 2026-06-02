from __future__ import annotations

import base64
import pytest

from aworld.evaluations.base import EvaluationConfig
import aworld.evaluations.substrate as substrate_module
from aworld.evaluations.substrate import (
    AgentJudgeBackend,
    CallableJudgeBackend,
    EvalCaseDef,
    EvalSuiteDef,
    EvaluationFlowDef,
    FallbackJudgeBackend,
    GatePolicyDef,
    JudgeSchemaDef,
    compile_evaluation_flow,
    get_builtin_eval_suite,
    list_eval_suites,
    register_eval_suite,
    resolve_eval_suite,
    run_evaluation_flow,
)


def test_compile_evaluation_flow_builds_inline_dataset_and_gate_config() -> None:
    suite = EvalSuiteDef(
        suite_id="demo-suite",
        cases=[
            EvalCaseDef(
                case_id="case-1",
                input={"query": "hello world"},
            )
        ],
        judge_schema=JudgeSchemaDef(required_fields=("score", "rank")),
        gate_policy=GatePolicyDef(metric_name="score", pass_threshold=0.8),
    )
    flow = EvaluationFlowDef(
        target={"kind": "inline", "value": {"target_path": "demo.txt"}},
        suite=suite,
    )

    compiled = compile_evaluation_flow(flow)

    assert isinstance(compiled.eval_config, EvaluationConfig)
    assert compiled.eval_config.eval_dataset is compiled.dataset
    assert compiled.dataset.eval_cases[0].case_data["query"] == "hello world"
    assert compiled.dataset.eval_cases[0].case_data["_target"]["target_path"] == "demo.txt"
    assert compiled.gate_policy.metric_name == "score"


def test_judge_schema_validation_rejects_missing_fields() -> None:
    schema = JudgeSchemaDef(required_fields=("score", "rank", "criticism"))

    with pytest.raises(ValueError, match="missing required judge fields"):
        schema.validate({"score": 0.8, "rank": "Good"})


def test_gate_policy_uses_pass_and_approval_thresholds() -> None:
    gate = GatePolicyDef(
        metric_name="score",
        pass_threshold=0.85,
        approval_threshold=0.6,
    )

    assert gate.evaluate({"score": 0.9}).status == "pass"
    assert gate.evaluate({"score": 0.7}).status == "needs_approval"
    assert gate.evaluate({"score": 0.5}).status == "fail"


@pytest.mark.asyncio
async def test_run_evaluation_flow_executes_suite_judge_and_returns_gate() -> None:
    async def fake_judge(case_input, target):
        assert case_input["query"] == "hello"
        assert target["target_path"] == "artifact.txt"
        return {
            "score": 0.7,
            "rank": "Good",
            "criticism": "Needs stronger hierarchy.",
            "praise": "The layout is clear.",
            "improvement_advice": "Increase contrast around the hero area.",
        }

    suite = EvalSuiteDef(
        suite_id="demo-suite",
        cases=[EvalCaseDef(case_id="case-1", input={"query": "hello"})],
        judge_schema=JudgeSchemaDef(
            required_fields=(
                "score",
                "rank",
                "criticism",
                "praise",
                "improvement_advice",
            )
        ),
        gate_policy=GatePolicyDef(
            metric_name="score",
            pass_threshold=0.85,
            approval_threshold=0.6,
        ),
        judge=fake_judge,
    )
    flow = EvaluationFlowDef(
        target={"kind": "file", "target_path": "artifact.txt"},
        suite=suite,
    )

    report = await run_evaluation_flow(flow)

    assert report["suite_id"] == "demo-suite"
    assert report["gate"]["status"] == "needs_approval"
    assert report["results"][0]["judge"]["rank"] == "Good"
    assert report["summary"]["demo-suite"]["score"]["mean"] == pytest.approx(0.7)


def test_builtin_app_evaluator_suite_has_required_schema_and_score_gate() -> None:
    suite = get_builtin_eval_suite("app-evaluator")

    assert suite.suite_id == "app-evaluator"
    assert suite.judge_schema.required_fields == (
        "score",
        "rank",
        "criticism",
        "praise",
        "improvement_advice",
    )
    assert suite.gate_policy.metric_name == "score"


def test_eval_suite_registry_resolves_explicit_and_target_defaults(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    monkeypatch.setattr(substrate_module, "_EVAL_SUITE_REGISTRY", {})

    def generic_factory(target):
        return EvalSuiteDef(suite_id="generic-review")

    def image_factory(target):
        return EvalSuiteDef(suite_id="image-review")

    register_eval_suite(
        "generic-review",
        generic_factory,
        matcher=lambda target: True,
        priority=10,
    )
    register_eval_suite(
        "image-review",
        image_factory,
        matcher=lambda target: target["target_kind"] == "image",
        priority=50,
    )

    image_target = tmp_path / "artifact.png"
    image_target.write_bytes(base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aA1EAAAAASUVORK5CYII="))
    text_target = tmp_path / "artifact.txt"
    text_target.write_text("artifact", encoding="utf-8")

    listed = list_eval_suites()
    explicit = resolve_eval_suite("generic-review", image_target)
    image_default = resolve_eval_suite(None, image_target)
    text_default = resolve_eval_suite(None, text_target)

    assert listed == ["generic-review", "image-review"]
    assert explicit.suite_id == "generic-review"
    assert image_default.suite_id == "image-review"
    assert image_default.cases[0].input["target_kind"] == "image"
    assert text_default.suite_id == "generic-review"
    assert text_default.cases[0].input["target_kind"] == "file"


@pytest.mark.asyncio
async def test_agent_judge_backend_parses_app_evaluator_json_payload() -> None:
    async def fake_executor(prompt: str, system_prompt: str):
        assert "artifact.txt" in prompt
        assert "UI review committee" in system_prompt
        return {
            "results": [
                {
                    "filename": "artifact.txt",
                    "score": 0.91,
                    "rank": "Exemplary",
                    "criticism": "Almost none.",
                    "praise": "Strong visual hierarchy.",
                    "improvement_advice": "Keep the current direction.",
                }
            ]
        }

    backend = AgentJudgeBackend(
        backend_id="agent-backend",
        system_prompt="You are a UI review committee.",
        executor=fake_executor,
    )

    payload = await backend.judge(
        case_input={"target_path": "artifact.txt"},
        target={"target_path": "artifact.txt", "target_kind": "file"},
        suite=EvalSuiteDef(suite_id="app-evaluator"),
    )

    assert payload["score"] == pytest.approx(0.91)
    assert payload["rank"] == "Exemplary"


@pytest.mark.asyncio
async def test_builtin_app_evaluator_can_use_injected_judge_backend() -> None:
    class StubBackend:
        backend_id = "stub-agent"

        def is_available(self) -> bool:
            return True

        async def judge(self, case_input, target, suite):
            return {
                "score": 0.72,
                "rank": "Good",
                "criticism": "Needs slightly better spacing.",
                "praise": "Solid composition.",
                "improvement_advice": "Increase whitespace around the main section.",
            }

    suite = get_builtin_eval_suite("app-evaluator", judge_backend=StubBackend()).with_cases(
        [EvalCaseDef(case_id="artifact", input={"target_path": "artifact.txt"})]
    )
    flow = EvaluationFlowDef(
        target={"target_path": "artifact.txt", "target_kind": "file"},
        suite=suite,
    )

    report = await run_evaluation_flow(flow)

    assert report["judge_backend"]["backend_id"] == "stub-agent"
    assert report["results"][0]["judge"]["rank"] == "Good"
    assert report["report_version"] == 1
    assert report["approval"]["required"] is True


@pytest.mark.asyncio
async def test_fallback_judge_backend_uses_next_backend_after_timeout() -> None:
    async def slow_executor(prompt: str, system_prompt: str):
        await asyncio.sleep(0.05)
        return {"results": [{"filename": "artifact.txt", "score": 0.99}]}

    fallback = FallbackJudgeBackend(
        backend_id="fallback",
        backends=(
            AgentJudgeBackend(
                backend_id="slow-agent",
                system_prompt="judge",
                executor=slow_executor,
                timeout_seconds=0.01,
            ),
            CallableJudgeBackend(
                backend_id="heuristic",
                judge=lambda case_input, target: {
                    "score": 0.61,
                    "rank": "Good",
                    "criticism": "Fallback path used.",
                    "praise": "Fallback stayed responsive.",
                    "improvement_advice": "Keep timeout budgets explicit.",
                },
            ),
        ),
    )

    execution = await fallback.execute(
        case_input={"target_path": "artifact.txt"},
        target={"target_path": "artifact.txt", "target_kind": "file"},
        suite=EvalSuiteDef(suite_id="app-evaluator"),
    )

    assert execution.backend_id == "heuristic"
    assert execution.payload["score"] == pytest.approx(0.61)


@pytest.mark.asyncio
async def test_builtin_app_evaluator_passes_visual_target_images_to_agent_backend(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    image_path = tmp_path / "artifact.png"
    image_path.write_bytes(
        base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aA1EAAAAASUVORK5CYII="
        )
    )

    captured = {}

    async def fake_executor(prompt, system_prompt: str):
        captured["prompt"] = prompt
        return {
            "results": [
                {
                    "filename": image_path.name,
                    "score": 0.88,
                    "rank": "Exemplary",
                    "criticism": "Minor spacing polish remains.",
                    "praise": "The main visual is clear.",
                    "improvement_advice": "Tighten secondary detail spacing.",
                }
            ]
        }

    monkeypatch.setenv("LLM_MODEL_NAME", "test-model")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setattr(substrate_module, "_default_agent_judge_executor", fake_executor)

    suite = get_builtin_eval_suite("app-evaluator").with_cases(
        [
            EvalCaseDef(
                case_id="artifact",
                input={"target_path": str(image_path), "target_kind": "image"},
            )
        ]
    )
    flow = EvaluationFlowDef(
        target={"target_path": str(image_path), "target_kind": "image"},
        suite=suite,
    )

    report = await run_evaluation_flow(flow)

    prompt = captured["prompt"]

    assert isinstance(prompt, tuple)
    assert prompt[0].startswith("Evaluate the following app artifact")
    assert len(prompt[1]) == 1
    assert prompt[1][0].startswith("data:image/png;base64,")
    assert report["judge_backend"]["backend_id"] == "app-evaluator-agent"
