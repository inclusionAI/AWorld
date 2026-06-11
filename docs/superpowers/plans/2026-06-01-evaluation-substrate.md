# Evaluation Substrate Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build AWorld's internal evaluation substrate, add a working `aworld-cli evaluator` flow, and express `app_evaluator` as a suite-backed evaluation without breaking legacy evaluation APIs.

**Architecture:** Add a small internal definition-and-compilation layer under `aworld.evaluations`, keep execution on top of existing `EvaluateRunner`/`EvalTarget`/`Scorer`, then wire a new CLI top-level command onto that substrate. `app_evaluator` becomes the first built-in suite definition and gateable report flow.

**Tech Stack:** Python, dataclasses, existing AWorld evaluation runtime, argparse-based CLI, pytest, unittest-style async tests where already used

---

### Task 1: Add substrate contracts and red-path tests

**Files:**
- Create: `tests/evaluations/test_evaluation_substrate.py`
- Create: `aworld/evaluations/substrate.py`
- Modify: `aworld/config/conf.py`

- [ ] **Step 1: Write the failing substrate contract tests**

```python
from aworld.evaluations.substrate import (
    EvalSuiteDef,
    EvalCaseDef,
    JudgeSchemaDef,
    GatePolicyDef,
    EvaluationFlowDef,
    compile_evaluation_flow,
)


def test_compile_evaluation_flow_preserves_legacy_runner_inputs():
    suite = EvalSuiteDef(
        suite_id="demo-suite",
        cases=[EvalCaseDef(case_id="case-1", input={"query": "hello"})],
        judge_schema=JudgeSchemaDef(required_fields=("score", "rank")),
        gate_policy=GatePolicyDef(metric_name="score", pass_threshold=0.8),
    )
    flow = EvaluationFlowDef(target={"kind": "dict", "value": {"answer": "hello"}}, suite=suite)

    compiled = compile_evaluation_flow(flow)

    assert compiled.eval_config.eval_dataset_id_or_file_path is None
    assert compiled.dataset.eval_cases[0].case_data["query"] == "hello"
    assert compiled.gate_policy.metric_name == "score"
```

- [ ] **Step 2: Run the substrate tests to verify they fail**

Run: `pytest tests/evaluations/test_evaluation_substrate.py -q`
Expected: FAIL with import or missing symbol errors for the new substrate module and compile helpers.

- [ ] **Step 3: Implement the minimal substrate layer**

```python
@dataclass
class GatePolicyDef:
    metric_name: str
    pass_threshold: float
    approval_threshold: float | None = None


def compile_evaluation_flow(flow: EvaluationFlowDef) -> CompiledEvaluationPlan:
    dataset = build_eval_dataset(flow.suite.cases)
    eval_config = EvaluationConfig(
        eval_criterias=[],
        eval_dataset_id_or_file_path=None,
    )
    return CompiledEvaluationPlan(
        suite=flow.suite,
        dataset=dataset,
        eval_config=eval_config,
        gate_policy=flow.suite.gate_policy,
    )
```

- [ ] **Step 4: Re-run the substrate tests**

Run: `pytest tests/evaluations/test_evaluation_substrate.py -q`
Expected: PASS

- [ ] **Step 5: Commit the substrate contract slice**

```bash
git add tests/evaluations/test_evaluation_substrate.py aworld/evaluations/substrate.py aworld/config/conf.py
git commit -m "feat: add evaluation substrate contracts"
```

### Task 2: Add schema validation and gate decisions

**Files:**
- Modify: `tests/evaluations/test_evaluation_substrate.py`
- Modify: `aworld/evaluations/substrate.py`

- [ ] **Step 1: Write failing tests for schema validation and gate outcomes**

```python
def test_gate_policy_returns_needs_approval_between_thresholds():
    decision = GatePolicyDef(
        metric_name="score",
        pass_threshold=0.85,
        approval_threshold=0.6,
    ).evaluate({"score": 0.7})

    assert decision.status == "needs_approval"


def test_judge_schema_rejects_missing_required_fields():
    schema = JudgeSchemaDef(required_fields=("score", "rank", "criticism"))

    with pytest.raises(ValueError, match="missing required judge fields"):
        schema.validate({"score": 0.8, "rank": "Good"})
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/evaluations/test_evaluation_substrate.py -q`
Expected: FAIL because schema validation and gate evaluation are not implemented yet.

- [ ] **Step 3: Implement schema validation and gate decisions**

```python
def validate(self, payload: Mapping[str, Any]) -> None:
    missing = [field for field in self.required_fields if field not in payload]
    if missing:
        raise ValueError(f"missing required judge fields: {', '.join(missing)}")


def evaluate(self, metrics: Mapping[str, Any]) -> GateDecision:
    score = float(metrics[self.metric_name])
    if score >= self.pass_threshold:
        return GateDecision(status="pass", metric_name=self.metric_name, value=score)
    if self.approval_threshold is not None and score >= self.approval_threshold:
        return GateDecision(status="needs_approval", metric_name=self.metric_name, value=score)
    return GateDecision(status="fail", metric_name=self.metric_name, value=score)
```

- [ ] **Step 4: Re-run the substrate tests**

Run: `pytest tests/evaluations/test_evaluation_substrate.py -q`
Expected: PASS

- [ ] **Step 5: Commit the schema-and-gate slice**

```bash
git add tests/evaluations/test_evaluation_substrate.py aworld/evaluations/substrate.py
git commit -m "feat: add evaluation schema and gate decisions"
```

### Task 3: Add the CLI evaluator command

**Files:**
- Create: `tests/core/test_evaluator_top_level_command.py`
- Create: `aworld-cli/src/aworld_cli/top_level_commands/evaluator_cmd.py`
- Create: `aworld-cli/src/aworld_cli/evaluator_runtime.py`
- Modify: `aworld-cli/src/aworld_cli/top_level_commands/__init__.py`
- Modify: `aworld-cli/src/aworld_cli/main.py`

- [ ] **Step 1: Write the failing evaluator CLI tests**

```python
def test_registry_registers_builtin_evaluator_command():
    registry = main_module._build_top_level_command_registry()
    command = registry.get("evaluator")
    assert command is not None


def test_maybe_dispatch_top_level_command_runs_evaluator(monkeypatch, tmp_path, capsys):
    target = tmp_path / "artifact.txt"
    target.write_text("demo", encoding="utf-8")
    monkeypatch.setattr(
        "aworld_cli.top_level_commands.evaluator_cmd.run_evaluator_cli",
        lambda **kwargs: {"gate": {"status": "pass"}, "suite_id": "app-evaluator"},
    )

    handled = main_module._maybe_dispatch_top_level_command(
        ["aworld-cli", "evaluator", "--target", str(target)]
    )

    assert handled is True
```

- [ ] **Step 2: Run the evaluator CLI tests to verify they fail**

Run: `pytest tests/core/test_evaluator_top_level_command.py -q`
Expected: FAIL because the evaluator command is not registered yet.

- [ ] **Step 3: Implement the minimal evaluator command and runtime**

```python
class EvaluatorTopLevelCommand:
    @property
    def name(self) -> str:
        return "evaluator"

    def register_parser(self, subparsers) -> None:
        parser = subparsers.add_parser("evaluator", help=self.description)
        parser.add_argument("--target", required=True)
        parser.add_argument("--suite")
        parser.add_argument("--output")

    def run(self, args, context) -> int:
        result = run_evaluator_cli(target=args.target, suite=args.suite, output=args.output)
        print(render_evaluator_summary(result))
        return 0
```

- [ ] **Step 4: Re-run the evaluator CLI tests**

Run: `pytest tests/core/test_evaluator_top_level_command.py -q`
Expected: PASS

- [ ] **Step 5: Commit the evaluator command slice**

```bash
git add tests/core/test_evaluator_top_level_command.py aworld-cli/src/aworld_cli/top_level_commands/evaluator_cmd.py aworld-cli/src/aworld_cli/evaluator_runtime.py aworld-cli/src/aworld_cli/top_level_commands/__init__.py aworld-cli/src/aworld_cli/main.py
git commit -m "feat: add evaluator top level command"
```

### Task 4: Add the built-in app evaluator suite and end-to-end report wiring

**Files:**
- Create: `tests/evaluations/test_app_evaluator_suite.py`
- Modify: `aworld/evaluations/substrate.py`
- Modify: `aworld-skills/app_evaluator/SKILL.md`
- Modify: `aworld-cli/src/aworld_cli/evaluator_runtime.py`

- [ ] **Step 1: Write the failing app evaluator suite tests**

```python
from aworld.evaluations.substrate import get_builtin_eval_suite


def test_app_evaluator_suite_requires_expected_judge_fields():
    suite = get_builtin_eval_suite("app-evaluator")

    assert suite.judge_schema.required_fields == (
        "score",
        "rank",
        "criticism",
        "praise",
        "improvement_advice",
    )


def test_app_evaluator_suite_uses_threshold_gate():
    suite = get_builtin_eval_suite("app-evaluator")

    assert suite.gate_policy.metric_name == "score"
```

- [ ] **Step 2: Run the app evaluator suite tests to verify they fail**

Run: `pytest tests/evaluations/test_app_evaluator_suite.py -q`
Expected: FAIL because the builtin app evaluator suite registry does not exist yet.

- [ ] **Step 3: Implement the builtin app evaluator suite and CLI result persistence**

```python
def get_builtin_eval_suite(name: str) -> EvalSuiteDef:
    if name != "app-evaluator":
        raise KeyError(name)
    return EvalSuiteDef(
        suite_id="app-evaluator",
        judge_schema=JudgeSchemaDef(
            required_fields=(
                "score",
                "rank",
                "criticism",
                "praise",
                "improvement_advice",
            )
        ),
        gate_policy=GatePolicyDef(metric_name="score", pass_threshold=0.8, approval_threshold=0.6),
    )
```

- [ ] **Step 4: Re-run the app evaluator suite tests and the focused CLI/substrate suite**

Run: `pytest tests/evaluations/test_app_evaluator_suite.py tests/evaluations/test_evaluation_substrate.py tests/core/test_evaluator_top_level_command.py -q`
Expected: PASS

- [ ] **Step 5: Commit the built-in suite slice**

```bash
git add tests/evaluations/test_app_evaluator_suite.py aworld/evaluations/substrate.py aworld-skills/app_evaluator/SKILL.md aworld-cli/src/aworld_cli/evaluator_runtime.py
git commit -m "feat: add builtin app evaluator suite"
```

### Task 5: Full focused verification

**Files:**
- Test: `tests/evaluations/test_evaluation_substrate.py`
- Test: `tests/evaluations/test_app_evaluator_suite.py`
- Test: `tests/core/test_evaluator_top_level_command.py`
- Test: `tests/evaluations/test_dataset_evaluate.py`

- [ ] **Step 1: Run the focused verification suite**

Run: `pytest tests/evaluations/test_evaluation_substrate.py tests/evaluations/test_app_evaluator_suite.py tests/core/test_evaluator_top_level_command.py tests/evaluations/test_dataset_evaluate.py -q`
Expected: PASS

- [ ] **Step 2: Sanity-check the CLI help output**

Run: `python -m aworld_cli.main evaluator --help`
Expected: exit 0 and help output showing `--target`, `--suite`, and `--output`

- [ ] **Step 3: Validate the OpenSpec change remains consistent**

Run: `openspec validate aworld-evaluation-substrate-2026-06-01`
Expected: `Change 'aworld-evaluation-substrate-2026-06-01' is valid`
