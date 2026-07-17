# Framework-Native Self-Evolve Reliability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make trajectory-backed self-evolve reliably generate, validate, evaluate, and auto-apply skill-owned candidates through existing AWorld runtime primitives without target-specific framework logic.

**Architecture:** A typed `EvolutionContext` compiles bounded optimizer evidence and registered capability authoring contracts into candidate Task input. A canonical candidate protocol normalizes safe response variants before compact repair, while existing AWorld Task/Runner, isolated Context, prompt budgeting, deterministic batch execution, replay overlays, gates, and apply journals remain authoritative. Typed validation outcomes drive at most the configured number of improvement iterations and distinguish candidate rejection from infrastructure failure.

**Tech Stack:** Python 3.12, dataclasses, Pydantic, AWorld `Factory`, AWorld Task/Runner/Context APIs, pytest/pytest-asyncio.

---

## File Map

- Create `aworld/self_evolve/candidate_protocol.py`: canonical schema, bounded JSON extraction, deterministic normalization, and typed protocol diagnostics.
- Create `aworld/self_evolve/evolution_context.py`: bounded typed candidate-generation input and general population strategy planning.
- Create `aworld/self_evolve/capability_contracts.py`: AWorld `Factory`-backed authoring-contract provider API and replay provider.
- Modify `aworld/self_evolve/optimizers/base.py`: carry compiled context in `OptimizerRequest` without changing dataset ownership.
- Modify `aworld/self_evolve/optimizers/llm_mutator.py`: serialize compiled context and canonical output contract; remove feedback-string prompt branches.
- Modify `aworld/self_evolve/concurrency.py`: compact repair inputs, typed slot outcomes, and complete repair accounting.
- Modify `aworld/runners/batch.py`: retain bounded usage metadata when indexed fail-fast discards response content.
- Modify `aworld/self_evolve/runner.py`: compile evolution context, use typed iteration/status semantics, and preserve infrastructure diagnostics.
- Modify `aworld/self_evolve/release_checks.py`: keep skipped check groups at `not_run`.
- Add focused tests under `tests/self_evolve/` and `tests/runners/`; extend existing CLI runner tests for default iteration behavior.

### Task 1: Canonical Candidate Package Protocol

**Files:**
- Create: `aworld/self_evolve/candidate_protocol.py`
- Create: `tests/self_evolve/test_candidate_protocol.py`
- Modify: `aworld/self_evolve/runner.py:2484-2545`

- [ ] **Step 1: Write failing normalization tests**

```python
def test_normalize_legacy_contract_envelope():
    raw = {"candidate_output_contract": {"patch_intent": {"operations": []}, "files": []}}
    normalized = normalize_candidate_output(raw, current_content="# Skill\n")
    assert normalized["schema_version"] == CANDIDATE_SCHEMA_VERSION
    assert "candidate_output_contract" not in normalized

def test_normalize_one_json_object_with_surrounding_prose():
    raw = 'Result follows:\n{"content":"# Skill\\nNew","files":[]}\nDone.'
    assert normalize_candidate_output(raw, current_content="# Skill\n")["content"].endswith("New")

def test_normalize_rejects_conflicting_direct_and_envelope_fields():
    with pytest.raises(CandidateProtocolError, match="conflicting"):
        normalize_candidate_output(
            {"content": "direct", "candidate_output_contract": {"content": "nested"}},
            current_content="# Skill\n",
        )

def test_normalize_rejects_multiple_json_objects():
    with pytest.raises(CandidateProtocolError, match="one JSON object"):
        normalize_candidate_output('{"content":"a"} {"content":"b"}', current_content="# Skill\n")
```

- [ ] **Step 2: Run the protocol tests and verify RED**

Run: `pytest -q tests/self_evolve/test_candidate_protocol.py`

Expected: collection/import failure because `candidate_protocol` does not exist.

- [ ] **Step 3: Implement the canonical protocol and bounded normalizer**

```python
CANDIDATE_SCHEMA_VERSION = "aworld.self_evolve.candidate.v1"
CANDIDATE_OUTPUT_CONTRACT = {
    "schema_version": CANDIDATE_SCHEMA_VERSION,
    "content": "optional complete primary target content",
    "patch_intent": {"operations": []},
    "rationale": "bounded explanation",
    "files": [],
}

class CandidateProtocolError(ValueError):
    def __init__(self, code: str, message: str, *, field_path: str | None = None):
        self.code = code
        self.field_path = field_path
        super().__init__(message)

def normalize_candidate_output(raw_output: Any, *, current_content: str) -> dict[str, Any]:
    payload = _decode_single_object(raw_output)
    envelope = payload.get("candidate_output_contract")
    if envelope is not None:
        if not isinstance(envelope, Mapping):
            raise CandidateProtocolError("invalid_envelope", "candidate envelope must be an object")
        direct = {key: payload[key] for key in _CANDIDATE_FIELDS if key in payload}
        conflicts = [key for key in direct if key in envelope and direct[key] != envelope[key]]
        if conflicts:
            raise CandidateProtocolError("conflicting_envelope", "conflicting direct and envelope fields")
        payload = {**dict(envelope), **direct}
    return _validate_canonical_payload(payload, current_content=current_content)
```

Use `json.JSONDecoder().raw_decode()` plus bounded prefix/suffix checks to accept exactly one object; reuse `apply_skill_patch_intent()` and `validate_candidate_files()` for existing package semantics.

- [ ] **Step 4: Route runner parsing through the protocol**

```python
def _parse_candidate_mutation_model_output(raw_output: Any, *, current_content: str) -> Mapping[str, Any]:
    return normalize_candidate_output(raw_output, current_content=current_content)
```

- [ ] **Step 5: Run protocol and existing optimizer tests and verify GREEN**

Run: `pytest -q tests/self_evolve/test_candidate_protocol.py tests/self_evolve/test_optimizer_contract.py`

Expected: all selected tests pass.

- [ ] **Step 6: Commit the protocol increment**

```bash
git add aworld/self_evolve/candidate_protocol.py aworld/self_evolve/runner.py tests/self_evolve/test_candidate_protocol.py
git commit -m "feat(self-evolve): normalize candidate package protocol"
```

### Task 2: Evolution Context and Capability Authoring Contracts

**Files:**
- Create: `aworld/self_evolve/evolution_context.py`
- Create: `aworld/self_evolve/capability_contracts.py`
- Create: `tests/self_evolve/test_evolution_context.py`
- Create: `tests/self_evolve/test_capability_contracts.py`
- Modify: `aworld/self_evolve/optimizers/base.py`

- [ ] **Step 1: Write failing context and registry tests**

```python
def test_compiler_deduplicates_feedback_and_selects_capability_strategy():
    context = compile_evolution_context(_request_with_duplicate_feedback_and_replay_requirement())
    assert len(context.validation_feedback) == 1
    assert "missing_capability_completion" in context.population_strategies
    assert "agent-browser" not in json.dumps(context.to_prompt_payload())

def test_replay_contract_comes_from_protocol_constants():
    provider = capability_contract_factory("replay")
    contract = provider.authoring_contract((_generic_replay_requirement(),))
    assert contract["manifest"]["schema_version"] == REPLAY_CAPABILITY_SCHEMA_VERSION
    assert contract["compiler"]["protocol_version"] == REPLAY_CAPABILITY_PROTOCOL_VERSION

def test_factory_registration_supports_external_provider():
    @capability_contract_factory.register("test-capability")
    class Provider:
        capability_type = "test-capability"
    assert capability_contract_factory.get_class("test-capability") is Provider
```

- [ ] **Step 2: Run the new tests and verify RED**

Run: `pytest -q tests/self_evolve/test_evolution_context.py tests/self_evolve/test_capability_contracts.py`

Expected: imports fail because the two modules do not exist.

- [ ] **Step 3: Implement the Factory-backed provider API**

```python
class CandidateCapabilityContractProvider(Protocol):
    capability_type: str
    def applies_to(self, requirements: Sequence[object]) -> bool: ...
    def authoring_contract(self, requirements: Sequence[object]) -> Mapping[str, object]: ...
    def validate_candidate(self, candidate: CandidateVariant) -> CapabilityValidationResult: ...

capability_contract_factory: Factory[type[CandidateCapabilityContractProvider]] = Factory(
    "self-evolve capability contract provider"
)

@capability_contract_factory.register("replay")
class ReplayCapabilityContractProvider:
    capability_type = "replay"
```

Build the replay authoring contract only from constants and generic parsers already defined in `replay_capability.py`; include manifest path, schema versions, subprocess arguments, request/result fields, and deterministic compile constraints, never a domain adapter implementation.

- [ ] **Step 4: Implement typed context compilation**

```python
@dataclass(frozen=True)
class EvolutionContext:
    target: Mapping[str, object]
    current_content: str
    target_package_inventory: tuple[str, ...]
    trainable_cases: tuple[Mapping[str, object], ...]
    trace_evidence: tuple[Mapping[str, object], ...]
    validation_feedback: tuple[Mapping[str, object], ...]
    lesson_records: tuple[Mapping[str, object], ...]
    capability_requirements: tuple[Mapping[str, object], ...]
    capability_contracts: tuple[Mapping[str, object], ...]
    population_strategies: tuple[str, ...]
    expected_output: Mapping[str, object]

def compile_evolution_context(request: OptimizerRequest) -> EvolutionContext:
    normalized = _deduplicate_feedback(request.validation_feedback, request.prior_feedback)
    contracts = discover_applicable_contracts(request.replay_requirements)
    strategies = plan_population_strategies(normalized, contracts)
    return EvolutionContext(
        target=_target_payload(request),
        current_content=request.current_content,
        target_package_inventory=_bounded_inventory(request.target_package_inventory),
        trainable_cases=_trainable_case_payloads(request.trainable_cases),
        trace_evidence=_trace_evidence_payloads(request.trace_packs),
        validation_feedback=normalized,
        lesson_records=_lesson_payloads(request.lesson_records),
        capability_requirements=_requirement_payloads(request.replay_requirements),
        capability_contracts=contracts,
        population_strategies=strategies,
        expected_output=CANDIDATE_OUTPUT_CONTRACT,
    )
```

All list and text fields receive explicit item/character bounds and deterministic ordering. Add `evolution_context: EvolutionContext | None = None` to `OptimizerRequest` so custom optimizers remain compatible.

- [ ] **Step 5: Run new and request-contract tests and verify GREEN**

Run: `pytest -q tests/self_evolve/test_evolution_context.py tests/self_evolve/test_capability_contracts.py tests/self_evolve/test_optimizer_contract.py::test_optimizer_request_exposes_trainable_cases_without_held_out_leakage`

Expected: all selected tests pass.

- [ ] **Step 6: Commit the typed-context increment**

```bash
git add aworld/self_evolve/evolution_context.py aworld/self_evolve/capability_contracts.py aworld/self_evolve/optimizers/base.py tests/self_evolve/test_evolution_context.py tests/self_evolve/test_capability_contracts.py
git commit -m "feat(self-evolve): compile typed evolution context"
```

### Task 3: Contract-Driven Candidate Tasks and Compact Repair

**Files:**
- Modify: `aworld/self_evolve/optimizers/llm_mutator.py`
- Modify: `aworld/self_evolve/concurrency.py`
- Modify: `aworld/self_evolve/runner.py`
- Modify: `tests/self_evolve/test_optimizer_contract.py`
- Modify: `tests/self_evolve/test_candidate_population_execution.py`

- [ ] **Step 1: Write failing prompt and repair tests**

```python
async def test_mutator_prompt_serializes_compiled_context_without_feedback_keyword_branches():
    captured = []
    mutator = TraceReflectiveLLMMutator(mutate_text=lambda prompt: captured.append(prompt) or _candidate())
    await mutator.propose(_request_with_compiled_context())
    payload = json.loads(captured[0].split("\n", 1)[1])
    assert payload["expected_output"]["schema_version"] == CANDIDATE_SCHEMA_VERSION
    assert "candidate_output_contract" not in payload
    assert "If feedback mentions" not in captured[0]

async def test_repair_task_receives_schema_diagnostics_and_invalid_output_not_original_prompt():
    # Initial call returns invalid JSON; repair call records its Task input.
    assert "original trajectory sentinel" not in repair_task.input
    assert "invalid_response" in repair_task.input
    assert "candidate_schema" in repair_task.input
```

- [ ] **Step 2: Run the focused tests and verify RED**

Run: `pytest -q tests/self_evolve/test_optimizer_contract.py -k 'compiled_context' tests/self_evolve/test_candidate_population_execution.py -k 'repair_task_receives'`

Expected: assertions fail because prompts still contain the legacy contract and repair resends the full prompt.

- [ ] **Step 3: Replace case-specific prompt construction**

```python
def _build_mutation_prompt(request: OptimizerRequest, *, candidate_index: int) -> str:
    context = request.evolution_context or compile_evolution_context(request)
    payload = context.to_prompt_payload(candidate_index=candidate_index)
    return (
        "Generate one candidate package from this bounded EvolutionContext. "
        "Return the value of expected_output as one JSON object; do not wrap it.\n"
        + json.dumps(payload, ensure_ascii=False, sort_keys=True)
    )
```

Delete all `if feedback mentions ...` prose. Candidate strategy remains framework-owned and is selected from `context.population_strategies` by stable slot index.

- [ ] **Step 4: Change repair builder to a compact typed request**

```python
CandidateRepairPromptBuilder = Callable[[str, CandidateProtocolError], str]

def build_candidate_repair_prompt(invalid_response: str, error: CandidateProtocolError) -> str:
    payload = {
        "candidate_schema": CANDIDATE_OUTPUT_CONTRACT,
        "diagnostics": [error.to_diagnostic()],
        "invalid_response": invalid_response[:MAX_REPAIR_RESPONSE_CHARS],
    }
    return "Repair representation only. Return one candidate JSON object.\n" + json.dumps(payload, sort_keys=True)
```

Store the initial bounded raw response alongside the parse error; never pass the original generation prompt to repair. Continue building repair as a new Task from the same slot `CandidateGenerationAgent`, which preserves the existing AWorld lifecycle and isolated Context.

- [ ] **Step 5: Run optimizer and population tests and verify GREEN**

Run: `pytest -q tests/self_evolve/test_optimizer_contract.py tests/self_evolve/test_candidate_population_execution.py tests/self_evolve/test_candidate_generation.py`

Expected: all selected tests pass.

- [ ] **Step 6: Commit contract-driven generation**

```bash
git add aworld/self_evolve/optimizers/llm_mutator.py aworld/self_evolve/concurrency.py aworld/self_evolve/runner.py tests/self_evolve/test_optimizer_contract.py tests/self_evolve/test_candidate_population_execution.py
git commit -m "feat(self-evolve): drive candidate tasks from typed context"
```

### Task 4: Typed Candidate Outcomes, Iteration Budget, and Final Status

**Files:**
- Modify: `aworld/self_evolve/concurrency.py`
- Modify: `aworld/self_evolve/runner.py`
- Modify: `tests/self_evolve/test_candidate_population_execution.py`
- Modify: `tests/self_evolve/test_runner.py`

- [ ] **Step 1: Write failing outcome and CLI-default tests**

```python
async def test_second_protocol_violation_is_typed_candidate_rejection():
    result = await executor.run(["prompt"], max_concurrency=1)
    assert result.slots[0].status == "protocol_invalid"
    assert result.slots[0].failure["code"] == "candidate_protocol_invalid"

def test_auto_verified_without_explicit_iterations_uses_two_total_iterations(monkeypatch):
    assert _default_iteration_budget(apply_policy="auto_verified", explicit=None) == 2

def test_explicit_iteration_value_is_exact_upper_bound(monkeypatch):
    assert _default_iteration_budget(apply_policy="auto_verified", explicit=1) == 1

async def test_all_infrastructure_candidate_failures_finish_failed(tmp_path):
    status = _terminal_status(
        selected_state=None,
        population_diagnostics={"candidate_generation_failure": {"code": "provider_unavailable"}},
        post_apply=None,
    )
    assert status is SelfEvolveRunStatus.FAILED

async def test_all_protocol_invalid_candidates_finish_rejected(tmp_path):
    status = _terminal_status(
        selected_state=None,
        population_diagnostics={"protocol_invalid_count": 2},
        post_apply=None,
    )
    assert status is SelfEvolveRunStatus.REJECTED
```

- [ ] **Step 2: Run the focused tests and verify RED**

Run: `pytest -q tests/self_evolve/test_candidate_population_execution.py -k 'protocol_violation' tests/self_evolve/test_runner.py -k 'iterations or infrastructure_candidate or protocol_invalid'`

Expected: status/default assertions fail under current behavior.

- [ ] **Step 3: Add typed slot status and actionable-iteration helpers**

```python
CandidatePopulationStatus = Literal["succeeded", "protocol_invalid", "failed", "discarded"]

def _default_iteration_budget(*, apply_policy: str, explicit: int | None) -> int:
    if explicit is not None:
        if explicit <= 0:
            raise ValueError("iterations must be positive")
        return explicit
    return 2 if apply_policy == "auto_verified" else 1

def _has_only_actionable_candidate_failures(feedback: Sequence[EvaluationSummary]) -> bool:
    return bool(feedback) and all(
        item.metrics.get("failure_class") == "candidate" and item.metrics.get("repairable") is True
        for item in feedback
    )
```

Stop the next iteration on infrastructure diagnostics; allow it only for typed actionable candidate failures. Do not expose held-out records in `EvolutionContext`.

- [ ] **Step 4: Derive terminal status from typed evidence**

```python
def _terminal_status(*, selected_state, population_diagnostics, post_apply) -> SelfEvolveRunStatus:
    if post_apply and post_apply.get("status") == "accepted":
        return SelfEvolveRunStatus.SUCCEEDED
    if _has_deterministic_candidate_outcome(selected_state, population_diagnostics):
        return SelfEvolveRunStatus.REJECTED
    if _has_infrastructure_failure(population_diagnostics, selected_state):
        return SelfEvolveRunStatus.FAILED
    return SelfEvolveRunStatus.REJECTED
```

Persist a bounded `terminal_cause` with `failure_class`, stage, stable code, and root exception type. Do not persist model request/response content or credentials.

- [ ] **Step 5: Run runner, CLI, and outer Task tests and verify GREEN**

Run: `pytest -q tests/self_evolve/test_candidate_population_execution.py tests/self_evolve/test_runner.py tests/self_evolve/test_outer_task_runner.py tests/core/test_optimize_top_level_command.py`

Expected: all selected tests pass.

- [ ] **Step 6: Commit outcome semantics**

```bash
git add aworld/self_evolve/concurrency.py aworld/self_evolve/runner.py tests/self_evolve/test_candidate_population_execution.py tests/self_evolve/test_runner.py
git commit -m "fix(self-evolve): distinguish candidate and infrastructure outcomes"
```

### Task 5: Capability Validation Integration

**Files:**
- Modify: `aworld/self_evolve/capability_contracts.py`
- Modify: `aworld/self_evolve/runner.py`
- Modify: `tests/self_evolve/test_capability_contracts.py`
- Modify: `tests/self_evolve/test_skill_owned_replay_integration.py`

- [ ] **Step 1: Write failing validation-stage tests**

```python
def test_invalid_replay_manifest_returns_actionable_candidate_diagnostic(tmp_path):
    result = provider.validate_candidate(_candidate_with_invalid_manifest(tmp_path))
    assert result.passed is False
    assert result.diagnostics[0].stage == "capability_manifest"
    assert result.diagnostics[0].failure_class == "candidate"
    assert result.diagnostics[0].repairable is True

async def test_runner_queries_applicable_provider_without_target_switch(tmp_path):
    candidate = _candidate_with_test_capability(tmp_path)
    results = validate_applicable_capabilities(
        context=_context_with_test_capability(),
        candidate=candidate,
        providers=(provider,),
    )
    assert provider.seen_candidate_ids == [candidate.candidate_id]
    assert all(result.passed for result in results)
```

- [ ] **Step 2: Run capability integration tests and verify RED**

Run: `pytest -q tests/self_evolve/test_capability_contracts.py tests/self_evolve/test_skill_owned_replay_integration.py -k 'actionable or applicable_provider'`

Expected: validation API does not yet return staged typed diagnostics.

- [ ] **Step 3: Implement typed validation results**

```python
@dataclass(frozen=True)
class CandidateValidationDiagnostic:
    code: str
    stage: str
    failure_class: Literal["candidate", "infrastructure"]
    repairable: bool
    field_path: str | None = None

@dataclass(frozen=True)
class CapabilityValidationResult:
    passed: bool
    diagnostics: tuple[CandidateValidationDiagnostic, ...] = ()
```

The replay provider delegates manifest parsing, double compilation, fingerprinting, freezing, and provenance checks to existing `replay_capability.py` functions. It maps exceptions to typed bounded diagnostics; it does not import candidate code or add domain behavior.

- [ ] **Step 4: Invoke registered validation before authoritative replay**

Materialize the existing overlay, discover providers applicable to compiled context requirements, run their validation in deterministic provider-name order, and translate failed candidate diagnostics into gates/feedback. Infrastructure diagnostics terminate iteration without becoming mutation lessons.

- [ ] **Step 5: Run replay capability suites and verify GREEN**

Run: `pytest -q tests/self_evolve/test_capability_contracts.py tests/self_evolve/test_replay_capability.py tests/self_evolve/test_skill_owned_replay_integration.py tests/self_evolve/test_replay_overlay.py`

Expected: all selected tests pass.

- [ ] **Step 6: Commit registered capability validation**

```bash
git add aworld/self_evolve/capability_contracts.py aworld/self_evolve/runner.py tests/self_evolve/test_capability_contracts.py tests/self_evolve/test_skill_owned_replay_integration.py
git commit -m "feat(self-evolve): validate registered candidate capabilities"
```

### Task 6: Complete Batch Usage and Repair Telemetry

**Files:**
- Modify: `aworld/runners/batch.py`
- Modify: `aworld/self_evolve/concurrency.py`
- Modify: `aworld/self_evolve/runner.py`
- Modify: `tests/runners/test_deterministic_task_batch.py`
- Modify: `tests/self_evolve/test_candidate_population_execution.py`
- Modify: `tests/self_evolve/test_execution_telemetry.py`

- [ ] **Step 1: Write failing accounting tests**

```python
async def test_discarded_completed_result_retains_usage_without_response():
    results = await executor.run(items, max_concurrency=3, failure_policy="indexed_fail_fast")
    discarded = results[2]
    assert discarded.status == "discarded"
    assert discarded.response is None
    assert discarded.usage_metadata["total_tokens"] == 30

async def test_repair_telemetry_counts_attempt_success_and_tokens():
    diagnostics = (await population.run(["prompt"], max_concurrency=1)).diagnostics
    assert diagnostics["repair_attempt_count"] == 1
    assert diagnostics["repair_success_count"] == 1
    assert diagnostics["token_usage"]["total_tokens"] == 60
    assert diagnostics["repair_execution_seconds"] >= 0
```

- [ ] **Step 2: Run accounting tests and verify RED**

Run: `pytest -q tests/runners/test_deterministic_task_batch.py -k 'retains_usage' tests/self_evolve/test_candidate_population_execution.py -k 'repair_telemetry'`

Expected: `usage_metadata` and repair counters are absent.

- [ ] **Step 3: Add bounded batch usage metadata**

```python
@dataclass(frozen=True)
class TaskBatchResult:
    usage_metadata: Mapping[str, int] = field(default_factory=dict)

def _bounded_usage(response: TaskResponse | None) -> dict[str, int]:
    return {
        key: value for key, value in (response.usage if response else {}).items()
        if key in _ALLOWED_USAGE_KEYS and isinstance(value, int) and value >= 0
    }
```

Capture usage when the Task completes. When indexed fail-fast rewrites a higher-index result to `discarded`, clear `response` but retain `usage_metadata`.

- [ ] **Step 4: Aggregate initial and repair telemetry separately**

Add repair attempt/success/protocol-invalid/infrastructure-failure counts, initial and repair queue/execution seconds, cancellation/discard counts, and combined token usage. Extend `SelfEvolveExecutionTelemetry` allowlists and report aggregation without retaining answers.

- [ ] **Step 5: Run batch and telemetry suites and verify GREEN**

Run: `pytest -q tests/runners/test_deterministic_task_batch.py tests/self_evolve/test_candidate_population_execution.py tests/self_evolve/test_execution_telemetry.py`

Expected: all selected tests pass.

- [ ] **Step 6: Commit accounting changes**

```bash
git add aworld/runners/batch.py aworld/self_evolve/concurrency.py aworld/self_evolve/runner.py tests/runners/test_deterministic_task_batch.py tests/self_evolve/test_candidate_population_execution.py tests/self_evolve/test_execution_telemetry.py
git commit -m "fix(self-evolve): account for repair task usage"
```

### Task 7: Release Checklist and End-to-End Verification

**Files:**
- Modify: `aworld/self_evolve/release_checks.py`
- Modify: `tests/self_evolve/test_release_checks.py`
- Modify: `tests/self_evolve/test_runner.py`

- [ ] **Step 1: Write a failing skipped-check test**

```python
def test_all_skipped_release_groups_are_not_run():
    checklist = build_release_checklist(apply_policy="auto_verified", gate_results=[])
    assert checklist["status"] == "not_run"
    assert all(check["status"] == "not_run" for check in checklist["checks"])
```

- [ ] **Step 2: Run the release-check test and verify RED**

Run: `pytest -q tests/self_evolve/test_release_checks.py -k 'all_skipped'`

Expected: current aggregate status is `passed`.

- [ ] **Step 3: Correct aggregate checklist status**

```python
if any(check["status"] == "failed" for check in checks):
    status = "failed"
elif any(check["status"] == "passed" for check in checks):
    status = "passed"
else:
    status = "not_run"
```

- [ ] **Step 4: Run focused self-evolve regression suites**

Run: `pytest -q tests/self_evolve tests/runners/test_deterministic_task_batch.py tests/context/test_budgeted_prompt_assembly.py tests/context/test_local_isolated_application_context.py tests/agents/test_prompt_budgeted_agent.py tests/core/test_runtime_self_evolve_hooks.py tests/core/test_optimize_top_level_command.py`

Expected: all tests pass.

- [ ] **Step 5: Run repository-level verification**

Run: `python -m compileall -q aworld aworld-cli/src && git diff --check`

Expected: exit code 0 with no diff errors.

- [ ] **Step 6: Run the real black-box acceptance command**

Run:

```bash
conda run -n aworld_env aworld-cli optimize \
  --from-trajectory ~/Documents/trajectory.log \
  --apply auto_verified \
  --judge-agent ~/Documents/agent.md \
  --judge-timeout 600
```

Expected when external dependencies are available: CLI status `succeeded`, non-null selected candidate, all blocking gates passed, `post_apply.status == "accepted"`, and the installed skill package fingerprint matches the verified candidate.

If model, replay, or judge infrastructure is unavailable, expected behavior is status `failed` with a bounded infrastructure diagnostic. Do not claim end-to-end completion until the same command later reaches the success criteria.

- [ ] **Step 7: Commit release semantics after fresh verification**

```bash
git add aworld/self_evolve/release_checks.py tests/self_evolve/test_release_checks.py tests/self_evolve/test_runner.py
git commit -m "fix(self-evolve): report unevaluated release checks"
```

## Final Requirements Audit

- Candidate generation and repair run only through standard AWorld Agent/Task/Runner lifecycle.
- Prompt processing uses `PromptBudgetedAgent`, `BudgetedPromptAssemblyProvider`, and injected `ModelConfig`.
- Candidate slots and repair Tasks use `LocalIsolatedApplicationContext` and deterministic batch execution.
- No Browser/CDP, target identifier, trajectory fixture constant, or judge-specific behavior exists in framework production code.
- Replay capability implementation remains candidate/skill-owned; self-evolve publishes only its generic authoring contract.
- Baseline and candidate use the same frozen adaptation and isolated initial state.
- `succeeded` requires verified apply and accepted post-apply validation; infrastructure does not become `rejected` or candidate feedback.
- Reports include initial and repair Task usage, accurate skipped-check status, and no prompt/response/credential content.

### Task 8: Candidate Repair Conformance and Fixture-Derived Task-Plane Probes

**Files:**
- Create: `aworld/self_evolve/repair_conformance.py`
- Modify: `aworld/self_evolve/evolution_context.py`
- Modify: `aworld/self_evolve/optimizers/llm_mutator.py`
- Modify: `aworld/self_evolve/replay.py`
- Modify: `aworld/self_evolve/runner.py`
- Modify: `aworld/self_evolve/capability_contracts.py`
- Test: `tests/self_evolve/test_repair_conformance.py`
- Test: `tests/self_evolve/test_runner.py`
- Test: `tests/self_evolve/test_capability_contracts.py`

- [x] Compile the focused failed package and typed diagnostics into a bounded,
  serializable candidate-specific `RepairConformanceContract`.
- [x] Reject rationale-only or unrelated-file repairs unless a replay implementation
  source changes or the manifest redirects to a new non-empty runtime.
- [x] Validate exact failed probe constraints against the newly compiled frozen
  capability and execute its declared probes through the existing isolated replay
  service lifecycle.
- [x] For progressing task-plane timeouts, require a non-empty fixture-derived probe
  whose opaque request covers a late observed operation.
- [x] Feed conformance failures back as typed candidate repair evidence without
  launching paired task rollout.
- [ ] Run focused and full regression suites, then repeat the real optimize command
  until it reaches `succeeded` and `post_apply.status == "accepted"`.
