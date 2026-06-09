# AWorld Evaluator V2 Extensibility Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

> **Implementation status note:** This file records the original execution plan and is no longer the authoritative description of shipped behavior. The authoritative status is `tasks.md`, the delta spec, and the code/tests. Final implementation deliberately keeps declared JSON manifests metadata-only, defers builtin typed-model migration, treats `judge_schema` as an optional report-level object, and defines trajectory evaluation as single-shot `EvalState` inspection rather than verifiers-style rollout ownership.

**Goal:** Extend the framework-owned evaluator substrate with lightweight harness reuse, bounded program-backed execution, adapter-isolated runtime invocation, typed judge-output contracts, structured composite gate policies, and suite-declared trajectory scoring while keeping v1 evaluator flows compatible.

**Architecture:** Keep the current `EvalTarget -> Evaluator -> EvaluateRunner` skeleton, but move execution dispatch behind framework-owned adapters under `aworld/evaluations/`. Evolve suite-backed contracts additively: direct `suite.execution` lowers into a lightweight `EvalHarnessDef`, `EvalExecutionSpec` gains bounded import-callable `PROGRAM`, judge schemas gain typed-model support with a legacy bridge, gate policies gain structured composite conditions with lowering from the current threshold form, and trajectory scorer declarations lower into existing scorer criteria.

**Tech Stack:** Python, AWorld evaluation substrate under `aworld/evaluations/`, Pydantic v2 models already used in the repo, pytest, OpenSpec.

---

## File Structure

- `aworld/evaluations/execution.py`
  Extend execution mode definitions and shared normalization helpers used by all adapter paths.
- `aworld/evaluations/execution_adapters.py`
  New internal adapter boundary for static, agent, task, and program-backed execution.
- `aworld/evaluations/substrate.py`
  Compile suites onto harnesses/adapters, typed judge contracts, trajectory scorer criteria, and richer gate models while preserving compatibility.
- `aworld/evaluations/eval_targets/agent_eval.py`
  Reduce direct runtime coupling so existing eval targets align with adapter-backed execution.
- `aworld/evaluations/report.py`
  Surface typed judge schema metadata once at report level and structured gate outputs in the report contract where needed.
- `tests/evaluations/test_execution_state.py`
  Extend execution-state tests to cover program-backed normalization.
- `tests/evaluations/test_execution_adapters.py`
  New focused coverage for adapter selection and execution.
- `tests/evaluations/test_evaluation_substrate.py`
  Add substrate-level coverage for harness lowering, typed judge schemas, composite gates, trajectory scorers, and backward compatibility.
- `tests/core/test_evaluator_runtime.py`
  Guard that CLI-facing runtime assembly still works on top of the evolved framework substrate.
- `aworld/evaluations/README.md`
  Document the new framework-owned extension points once implementation settles.

### Task 1: Add harness lowering, execution adapters, and `PROGRAM` execution mode

**Files:**
- Modify: `aworld/evaluations/execution.py`
- Create: `aworld/evaluations/execution_adapters.py`
- Modify: `aworld/evaluations/substrate.py`
- Modify: `aworld/evaluations/eval_targets/agent_eval.py`
- Test: `tests/evaluations/test_execution_state.py`
- Test: `tests/evaluations/test_execution_adapters.py`
- Test: `tests/evaluations/test_evaluation_substrate.py`

- [ ] **Step 1: Write the failing harness, adapter, and program-execution tests**

```python
# tests/evaluations/test_execution_adapters.py
from __future__ import annotations

import pytest

from aworld.evaluations.execution import EvalExecutionMode, EvalExecutionSpec
from aworld.evaluations.execution_adapters import resolve_execution_adapter
from aworld.evaluations.substrate import EvalCaseDef


async def _demo_program(case, spec, target):
    return {
        "status": "success",
        "answer": f"ran:{case.input['query']}",
        "completion": [{"role": "assistant", "content": "final"}],
        "trajectory": [{"role": "assistant", "content": "step"}],
        "usage": {"total_tokens": 7},
    }


@pytest.mark.asyncio
async def test_program_execution_adapter_normalizes_result(monkeypatch):
    monkeypatch.setattr(
        "aworld.evaluations.execution_adapters.load_program_callable",
        lambda ref: _demo_program,
    )
    adapter = resolve_execution_adapter(
        EvalExecutionSpec(mode=EvalExecutionMode.PROGRAM, target_ref="pkg.module:run_case")
    )
    state = await adapter.execute(
        case=EvalCaseDef(case_id="case-1", input={"query": "demo"}),
        target={"target_kind": "directory"},
        spec=EvalExecutionSpec(mode=EvalExecutionMode.PROGRAM, target_ref="pkg.module:run_case"),
    )

    assert state.case_id == "case-1"
    assert state.answer == "ran:demo"
    assert state.completion[0]["content"] == "final"
    assert state.trajectory[0]["content"] == "step"
    assert state.usage["total_tokens"] == 7


def test_resolve_execution_adapter_rejects_missing_program_ref():
    with pytest.raises(ValueError, match="target_ref"):
        resolve_execution_adapter(EvalExecutionSpec(mode=EvalExecutionMode.PROGRAM))


def test_resolve_execution_adapter_rejects_command_style_program_ref():
    with pytest.raises(ValueError, match="importable callable"):
        resolve_execution_adapter(
            EvalExecutionSpec(mode=EvalExecutionMode.PROGRAM, target_ref="python script.py")
        )
```

```python
# tests/evaluations/test_execution_state.py
from aworld.evaluations.execution import normalize_task_response_to_eval_state


def test_normalize_mapping_response_preserves_completion_and_tool_calls():
    state = normalize_task_response_to_eval_state(
        case_id="case-2",
        response={
            "status": "success",
            "answer": "ok",
            "completion": [{"role": "assistant", "content": "ok"}],
            "trajectory": [{"tool_calls": [{"name": "search"}]}],
        },
    )

    assert state.completion[0]["content"] == "ok"
    assert state.tool_calls[0]["name"] == "search"
```

- [ ] **Step 2: Run the targeted tests and confirm they fail**

Run: `pytest tests/evaluations/test_execution_state.py tests/evaluations/test_execution_adapters.py -q`
Expected: FAIL because `EvalExecutionMode.PROGRAM`, `execution_adapters.py`, harness lowering, and adapter resolution do not exist yet.

- [ ] **Step 3: Add `PROGRAM` to execution definitions and create adapter implementations**

```python
# aworld/evaluations/execution.py
class EvalExecutionMode(str, Enum):
    STATIC = "static"
    AGENT = "agent"
    TASK = "task"
    PROGRAM = "program"


def load_program_callable(ref: str):
    if ":" in ref:
        module_name, attr_name = ref.split(":", 1)
    elif "." in ref:
        module_name, attr_name = ref.rsplit(".", 1)
    else:
        raise ValueError(f"invalid program ref: {ref}")
    module = importlib.import_module(module_name)
    return getattr(module, attr_name)
```

```python
# aworld/evaluations/execution_adapters.py
from __future__ import annotations

import inspect
from dataclasses import dataclass
from typing import Protocol

from aworld.evaluations.execution import (
    EvalExecutionMode,
    EvalExecutionSpec,
    EvalState,
    load_program_callable,
    normalize_task_response_to_eval_state,
)
from aworld.runner import Runners


class ExecutionAdapter(Protocol):
    async def execute(self, *, case, target: dict, spec: EvalExecutionSpec) -> EvalState:
        pass


@dataclass(frozen=True)
class StaticExecutionAdapter:
    async def execute(self, *, case, target: dict, spec: EvalExecutionSpec) -> EvalState:
        return EvalState(case_id=case.case_id, status="not_evaluated", metadata={"_target": dict(target)})


@dataclass(frozen=True)
class AgentExecutionAdapter:
    async def execute(self, *, case, target: dict, spec: EvalExecutionSpec) -> EvalState:
        query = case.input[spec.query_column or "query"]
        response = await Runners.run(query, agent=spec.target_config["agent"])
        return normalize_task_response_to_eval_state(case_id=case.case_id, response=response, target=target)


@dataclass(frozen=True)
class TaskExecutionAdapter:
    async def execute(self, *, case, target: dict, spec: EvalExecutionSpec) -> EvalState:
        builder = load_program_callable(spec.task_builder_ref)
        task = builder(case=case, target=target, spec=spec)
        if inspect.isawaitable(task):
            task = await task
        response = await Runners.run_task(task=task)
        return normalize_task_response_to_eval_state(case_id=case.case_id, response=response, target=target)


@dataclass(frozen=True)
class ProgramExecutionAdapter:
    async def execute(self, *, case, target: dict, spec: EvalExecutionSpec) -> EvalState:
        if not spec.target_ref:
            raise ValueError("program execution requires target_ref")
        program = load_program_callable(spec.target_ref)
        result = program(case, spec, target)
        if inspect.isawaitable(result):
            result = await result
        return normalize_task_response_to_eval_state(
            case_id=case.case_id,
            response=result,
            target=target,
            metadata={"_execution_mode": spec.mode.value},
        )


def resolve_execution_adapter(spec: EvalExecutionSpec) -> ExecutionAdapter:
    if spec.mode == EvalExecutionMode.STATIC:
        return StaticExecutionAdapter()
    if spec.mode == EvalExecutionMode.AGENT:
        return AgentExecutionAdapter()
    if spec.mode == EvalExecutionMode.TASK:
        return TaskExecutionAdapter()
    if spec.mode == EvalExecutionMode.PROGRAM:
        if not spec.target_ref:
            raise ValueError("program execution requires target_ref")
        return ProgramExecutionAdapter()
    raise ValueError(f"unsupported execution mode: {spec.mode}")
```

- [ ] **Step 4: Compile suite execution through lightweight harnesses and adapters in the substrate**

```python
# aworld/evaluations/substrate.py
from aworld.evaluations.execution_adapters import resolve_execution_adapter


@dataclass(frozen=True)
class EvalHarnessDef:
    harness_id: str
    execution: EvalExecutionSpec = field(default_factory=EvalExecutionSpec)
    metadata: dict[str, Any] = field(default_factory=dict)


def resolve_eval_harness(suite: EvalSuiteDef) -> EvalHarnessDef:
    if suite.harness is not None:
        return suite.harness
    if suite.execution is not None:
        return EvalHarnessDef(
            harness_id=f"{suite.suite_id}-execution",
            execution=suite.execution,
            metadata={"lowered_from": "suite.execution"},
        )
    return EvalHarnessDef(harness_id=f"{suite.suite_id}-static")
```

```python
# aworld/evaluations/substrate.py
class _AdapterExecutionEvalTarget(EvalTarget[dict]):
    async def predict(self, index: int, input: EvalDataCase[dict]) -> dict:
        case = EvalCaseDef(case_id=input.eval_case_id, input=dict(input.case_data))
        state = await self._adapter.execute(case=case, target=self._target, spec=self._harness.execution)
        return {"answer": state.answer, "state": state.to_dict()}
```

Adapters must not replace `EvalTarget -> Evaluator -> EvaluateRunner`; they only localize per-case invocation and normalization.

```python
# aworld/evaluations/eval_targets/agent_eval.py
class AworldTaskEvalTarget(EvalTarget[dict]):
    async def run_task_response(self, task: Task) -> TaskResponse | dict | object:
        return await Runners.run_task(task=task)
```

- [ ] **Step 5: Run adapter and substrate tests until green**

Run: `pytest tests/evaluations/test_execution_state.py tests/evaluations/test_execution_adapters.py tests/evaluations/test_evaluation_substrate.py -q`
Expected: PASS, including coverage for harness lowering, adapter-backed `PROGRAM` execution, rejected invalid program refs, and unchanged `static`/`agent`/`task` compatibility.

- [ ] **Step 6: Commit the execution-extensibility slice**

```bash
git add aworld/evaluations/execution.py aworld/evaluations/execution_adapters.py aworld/evaluations/substrate.py aworld/evaluations/eval_targets/agent_eval.py tests/evaluations/test_execution_state.py tests/evaluations/test_execution_adapters.py tests/evaluations/test_evaluation_substrate.py
git commit -m "feat: add adapter-backed evaluator execution"
```

### Task 2: Add typed judge-output contracts with a legacy compatibility bridge

**Files:**
- Modify: `aworld/evaluations/substrate.py`
- Modify: `aworld/evaluations/scorers/suite_judge.py`
- Modify: `aworld/evaluations/report.py`
- Test: `tests/evaluations/test_evaluation_substrate.py`
- Test: `tests/core/test_evaluator_runtime.py`

- [ ] **Step 1: Write failing tests for typed judge validation and legacy fallback**

```python
# tests/evaluations/test_evaluation_substrate.py
from pydantic import BaseModel

from aworld.evaluations.substrate import EvalSuiteDef, JudgeSchemaDef


class DemoJudgeOutput(BaseModel):
    score: float
    verdict: str


def test_typed_judge_model_accepts_valid_payload():
    suite = EvalSuiteDef(
        suite_id="demo",
        judge_schema=JudgeSchemaDef(output_model=DemoJudgeOutput),
    )

    payload = suite.judge_schema.validate_payload({"score": 0.8, "verdict": "ok"})
    assert payload["score"] == 0.8
    assert payload["verdict"] == "ok"


def test_typed_judge_model_rejects_invalid_payload():
    suite = EvalSuiteDef(
        suite_id="demo",
        judge_schema=JudgeSchemaDef(output_model=DemoJudgeOutput),
    )

    with pytest.raises(ValueError, match="verdict"):
        suite.judge_schema.validate_payload({"score": 0.8})


def test_legacy_required_fields_schema_still_validates():
    schema = JudgeSchemaDef(required_fields=("score", "rank"))
    payload = schema.validate_payload({"score": 0.9, "rank": 1})
    assert payload["rank"] == 1
```

- [ ] **Step 2: Run judge-schema tests to confirm failure**

Run: `pytest tests/evaluations/test_evaluation_substrate.py -q`
Expected: FAIL because `JudgeSchemaDef` does not yet support `output_model` or `validate_payload()`.

- [ ] **Step 3: Evolve `JudgeSchemaDef` into a typed contract with compatibility bridging**

```python
# aworld/evaluations/substrate.py
from pydantic import BaseModel, ValidationError


@dataclass(frozen=True)
class JudgeSchemaDef:
    required_fields: tuple[str, ...] = tuple()
    output_model: type[BaseModel] | None = None

    def validate_payload(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        if self.output_model is not None:
            try:
                model = self.output_model.model_validate(dict(payload))
            except ValidationError as exc:
                raise ValueError(str(exc)) from exc
            return model.model_dump(mode="json")

        missing = [field for field in self.required_fields if field not in payload]
        if missing:
            raise ValueError(f"missing required judge fields: {', '.join(missing)}")
        return dict(payload)

    def json_schema(self) -> dict[str, Any]:
        if self.output_model is not None:
            return self.output_model.model_json_schema()
        return {
            "type": "object",
            "required": list(self.required_fields),
            "properties": {field: {} for field in self.required_fields},
        }
```

- [ ] **Step 4: Route judge scoring and report metadata through the typed schema contract**

```python
# aworld/evaluations/scorers/suite_judge.py
payload = self.suite.judge_schema.validate_payload(dict(execution.payload))

metric_result = {
    "value": float(payload["score"]),
    "metadata": {
        **payload,
        "_judge_backend": execution.backend_id,
    },
}
```

```python
# aworld/evaluations/report.py
"judge_backend": {"type": "object"},
"judge_schema": {"type": ["object", "null"]},
```

`run_evaluation_flow()` should attach `report["judge_schema"] = suite.judge_schema.json_schema()` once at the top level when the schema is non-empty. Do not copy schema metadata into every case result.

- [ ] **Step 5: Run substrate and runtime tests until green**

Run: `pytest tests/evaluations/test_evaluation_substrate.py tests/core/test_evaluator_runtime.py -q`
Expected: PASS, including typed-model validation and unchanged legacy required-field flows.

- [ ] **Step 6: Commit the typed-judge-contract slice**

```bash
git add aworld/evaluations/substrate.py aworld/evaluations/scorers/suite_judge.py aworld/evaluations/report.py tests/evaluations/test_evaluation_substrate.py tests/core/test_evaluator_runtime.py
git commit -m "feat: add typed evaluator judge schemas"
```

### Task 3: Add structured composite gate policies with threshold compatibility lowering

**Files:**
- Modify: `aworld/evaluations/substrate.py`
- Modify: `aworld/evaluations/manifests.py`
- Modify: `aworld/evaluations/report.py`
- Test: `tests/evaluations/test_evaluation_substrate.py`
- Test: `tests/docs/test_evaluator_report_docs.py`

- [ ] **Step 1: Write failing tests for composite gates and legacy threshold compatibility**

```python
# tests/evaluations/test_evaluation_substrate.py
from aworld.evaluations.substrate import GateMetricCondition, GatePolicyDef


def test_composite_gate_returns_pass_when_all_conditions_hold():
    policy = GatePolicyDef(
        pass_all=(
            GateMetricCondition(metric_name="score", op=">=", threshold=0.9),
            GateMetricCondition(metric_name="latency", op="<=", threshold=5.0),
        )
    )

    decision = policy.evaluate({"score": 0.95, "latency": 4.2})
    assert decision.status == "pass"


def test_composite_gate_returns_needs_approval_when_approval_conditions_hold():
    policy = GatePolicyDef(
        pass_all=(GateMetricCondition(metric_name="score", op=">=", threshold=0.9),),
        approval_all=(GateMetricCondition(metric_name="score", op=">=", threshold=0.75),),
    )

    decision = policy.evaluate({"score": 0.8})
    assert decision.status == "needs_approval"


def test_legacy_threshold_gate_lowers_to_structured_policy():
    policy = GatePolicyDef(metric_name="score", pass_threshold=0.9, approval_threshold=0.8)
    decision = policy.evaluate({"score": 0.85})
    assert decision.status == "needs_approval"


@pytest.mark.parametrize(
    ("op", "threshold", "value"),
    [
        (">", 0.9, 0.91),
        ("<", 0.9, 0.89),
        (">=", 0.9, 0.9),
        ("<=", 0.9, 0.9),
        ("==", "approved", "approved"),
        ("!=", "blocked", "approved"),
    ],
)
def test_gate_metric_condition_supports_all_declared_operators(op, threshold, value):
    policy = GatePolicyDef(
        pass_all=(GateMetricCondition(metric_name="metric", op=op, threshold=threshold),)
    )

    assert policy.evaluate({"metric": value}).status == "pass"
```

- [ ] **Step 2: Run gate tests to confirm failure**

Run: `pytest tests/evaluations/test_evaluation_substrate.py -q`
Expected: FAIL because structured gate condition types do not exist yet.

- [ ] **Step 3: Add structured gate condition objects and compatibility lowering**

```python
# aworld/evaluations/substrate.py
@dataclass(frozen=True)
class GateMetricCondition:
    metric_name: str
    op: str
    threshold: float | int | str | bool

    def matches(self, metrics: Mapping[str, Any]) -> bool:
        value = metrics[self.metric_name]
        if self.op == ">=":
            return float(value) >= float(self.threshold)
        if self.op == "<=":
            return float(value) <= float(self.threshold)
        if self.op == ">":
            return float(value) > float(self.threshold)
        if self.op == "<":
            return float(value) < float(self.threshold)
        if self.op == "==":
            return value == self.threshold
        if self.op == "!=":
            return value != self.threshold
        raise ValueError(f"unsupported gate operator: {self.op}")


@dataclass(frozen=True)
class GatePolicyDef:
    metric_name: str | None = None
    pass_threshold: float | None = None
    approval_threshold: float | None = None
    pass_all: tuple[GateMetricCondition, ...] = tuple()
    approval_all: tuple[GateMetricCondition, ...] = tuple()

    def normalized_conditions(self) -> tuple[tuple[GateMetricCondition, ...], tuple[GateMetricCondition, ...]]:
        pass_all = self.pass_all
        approval_all = self.approval_all
        if not pass_all and self.metric_name is not None and self.pass_threshold is not None:
            pass_all = (GateMetricCondition(metric_name=self.metric_name, op=">=", threshold=self.pass_threshold),)
        if not approval_all and self.metric_name is not None and self.approval_threshold is not None:
            approval_all = (GateMetricCondition(metric_name=self.metric_name, op=">=", threshold=self.approval_threshold),)
        return pass_all, approval_all
```

Gate evaluation should collect every metric referenced by normalized pass/approval conditions. Missing metrics should raise a clear `KeyError` naming the metric, and unsupported operators should raise `ValueError`.

- [ ] **Step 4: Reflect the richer gate structure into manifests and report payloads**

```python
# aworld/evaluations/manifests.py
"gate_policy": {
    "type": "object",
    "properties": {
        "metric_name": {"type": "string"},
        "pass_threshold": {"type": "number"},
        "approval_threshold": {"type": ["number", "null"]},
        "pass_all": {"type": "array"},
        "approval_all": {"type": "array"},
    },
}
```

```python
# aworld/evaluations/report.py
"gateDecision": {
    "type": "object",
    "required": ["status", "metric_name", "value"],
    "properties": {
        "status": {"type": "string", "enum": ["pass", "fail", "needs_approval"]},
        "metric_name": {"type": ["string", "null"]},
        "value": {"type": ["number", "null"]},
        "matched_conditions": {"type": "array"},
        "failed_conditions": {"type": "array"},
    },
}
```

- [ ] **Step 5: Run gate and report-contract tests until green**

Run: `pytest tests/evaluations/test_evaluation_substrate.py tests/docs/test_evaluator_report_docs.py -q`
Expected: PASS, including both composite-gate and legacy-threshold cases.

- [ ] **Step 6: Commit the composite-gate slice**

```bash
git add aworld/evaluations/substrate.py aworld/evaluations/manifests.py aworld/evaluations/report.py tests/evaluations/test_evaluation_substrate.py tests/docs/test_evaluator_report_docs.py
git commit -m "feat: add composite evaluator gate policies"
```

### Task 4: Add trajectory scorer declarations, migrate builtin suites, document the new substrate, and run full verification

**Files:**
- Modify: `aworld/evaluations/substrate.py`
- Modify: `aworld/evaluations/README.md`
- Modify: `openspec/changes/aworld-evaluator-v2-extensibility-2026-06-09/tasks.md`
- Test: `tests/evaluations/test_execution_state.py`
- Test: `tests/evaluations/test_execution_adapters.py`
- Test: `tests/evaluations/test_evaluation_substrate.py`
- Test: `tests/core/test_evaluator_runtime.py`
- Test: `tests/core/test_evaluator_top_level_command.py`
- Test: `tests/plugins/test_plugin_hooks.py`
- Test: `tests/test_plugin_cli_entrypoint.py`
- Test: `tests/docs/test_evaluator_report_docs.py`

- [ ] **Step 1: Add suite-declared trajectory scorer lowering**

```python
# aworld/evaluations/substrate.py
@dataclass(frozen=True)
class TrajectoryScorerDef:
    metric_name: str
    scorer_class: str | None = None
    threshold: float = 0.0
    scorer_params: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class EvalSuiteDef:
    # ... existing fields ...
    trajectory_scorers: tuple[TrajectoryScorerDef, ...] = tuple()
```

`compile_evaluation_flow()` should append one `EvalCriteria` per trajectory scorer after the suite judge criterion. This reuses the existing scorer registry and report metric shape instead of creating a separate trajectory report path.

- [ ] **Step 2: Add or migrate a builtin suite to exercise the new contracts end to end**

```python
# aworld/evaluations/substrate.py
class AppEvaluatorJudgeOutput(BaseModel):
    score: float
    rank: int
    criticism: str
    praise: str
    improvement_advice: str


def get_builtin_eval_suite(suite_id: str) -> EvalSuiteDef:
    if suite_id == "app-evaluator":
        return EvalSuiteDef(
            suite_id="app-evaluator",
            judge=_app_evaluator_judge,
            judge_schema=JudgeSchemaDef(output_model=AppEvaluatorJudgeOutput),
            gate_policy=GatePolicyDef(
                pass_all=(GateMetricCondition(metric_name="score", op=">=", threshold=0.85),),
                approval_all=(GateMetricCondition(metric_name="score", op=">=", threshold=0.7),),
            ),
            metadata={"builtin": True, "preferred_backend": "callable"},
        )
```

- [ ] **Step 3: Update framework documentation and task checklist after implementation**

```md
<!-- aworld/evaluations/README.md -->
- `program`: execute an importable callable through the evaluator adapter layer
- typed judge schemas: Pydantic-backed validation with JSON schema export
- composite gates: structured conditions with compatibility for threshold-style suites
- trajectory scorers: suite-declared process metrics that lower into normal evaluator criteria
```

```md
<!-- openspec/changes/aworld-evaluator-v2-extensibility-2026-06-09/tasks.md -->
- [x] 1.0 Add a lightweight `EvalHarnessDef` boundary and compatibility lowering from direct `suite.execution`.
- [x] 1.1 Add a `PROGRAM` execution mode to the framework-owned evaluation execution model.
- [x] 1.2 Introduce an internal execution adapter boundary under `aworld/evaluations/` for static, agent, task, and program-backed execution.
- [x] 2.1 Add typed judge-output model support as the primary suite-backed validation contract.
- [x] 3.1 Expand gate definitions from single-threshold checks to structured composite metric conditions.
- [x] 4.1 Add suite-declared trajectory scorer definitions that lower into normal evaluator criteria.
```

- [ ] **Step 4: Run the full evaluator regression suite**

Run: `pytest tests/evaluations/test_execution_state.py tests/evaluations/test_execution_adapters.py tests/evaluations/test_evaluation_substrate.py tests/core/test_evaluator_runtime.py tests/core/test_evaluator_top_level_command.py tests/plugins/test_plugin_hooks.py tests/test_plugin_cli_entrypoint.py tests/docs/test_evaluator_report_docs.py -q`
Expected: PASS with all evaluator framework and CLI consumer tests green.

- [ ] **Step 5: Validate the OpenSpec change after code and docs are aligned**

Run: `openspec validate aworld-evaluator-v2-extensibility-2026-06-09 --strict`
Expected: `Change 'aworld-evaluator-v2-extensibility-2026-06-09' is valid`

- [ ] **Step 6: Commit the migration and verification slice**

```bash
git add aworld/evaluations/substrate.py aworld/evaluations/README.md openspec/changes/aworld-evaluator-v2-extensibility-2026-06-09/tasks.md tests/evaluations/test_execution_state.py tests/evaluations/test_execution_adapters.py tests/evaluations/test_evaluation_substrate.py tests/core/test_evaluator_runtime.py tests/core/test_evaluator_top_level_command.py tests/plugins/test_plugin_hooks.py tests/test_plugin_cli_entrypoint.py tests/docs/test_evaluator_report_docs.py
git commit -m "docs: finalize evaluator v2 extensibility rollout"
```

## Self-Review

- Spec coverage:
  - harness lowering, execution adapters, and `PROGRAM` mode -> Task 1
  - typed judge-output contracts with legacy compatibility -> Task 2
  - composite gate policies with threshold lowering -> Task 3
  - trajectory scorer declarations, verification, and spec alignment -> Task 4
- Placeholder scan:
  - no `TODO`, `TBD`, or "similar to previous task" shortcuts remain
  - remaining `...` tokens only appear inside Python variadic tuple type annotations, not as placeholders
  - each code-changing step contains concrete file paths and code snippets
- Type consistency:
  - `EvalExecutionMode.PROGRAM`, `ExecutionAdapter`, `JudgeSchemaDef.validate_payload`, `GateMetricCondition`, and `GatePolicyDef` names are used consistently across tasks
