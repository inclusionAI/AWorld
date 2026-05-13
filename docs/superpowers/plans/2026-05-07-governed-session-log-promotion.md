# Governed Session-Log Promotion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add governed promotion from workspace session logs into active durable memory without changing `AworldMemory` runtime message-memory semantics.

**Architecture:** Keep promotion governance inside the CLI durable-memory layer. Session logs remain the append-only truth source, the memory hook writes candidate and decision records, `CliDurableMemoryProvider` becomes the read/write surface for governed state and review actions, and `HybridMemoryProvider` only forwards new durable-memory APIs while leaving runtime `AworldMemory` behavior untouched.

**Tech Stack:** Python 3.10+, `pytest`, JSONL append-only storage, existing `aworld-cli` memory provider / hybrid provider / memory plugin command and hook surfaces.

---

## File Structure

### Create

- `aworld-cli/src/aworld_cli/memory/governance.py`
  Owns governance mode resolution, governed decision data model, source refs, policy gating, and append-only decision/review record helpers.
- `tests/cli_memory/test_governance.py`
  Covers policy modes, decision payload shape, duplicate / temporary blockers, and append-only review actions.

### Modify

- `aworld-cli/src/aworld_cli/memory/promotion.py`
  Keeps extraction helpers but drops boolean auto-promotion as the primary policy contract.
- `aworld-cli/src/aworld_cli/memory/metrics.py`
  Extends promotion metrics from simple counts to governed quality and threshold readiness.
- `aworld-cli/src/aworld_cli/memory/provider.py`
  Exposes governed decision listing, review actions, and active durable-memory reads that honor reverts.
- `aworld-cli/src/aworld_cli/memory/hybrid.py`
  Forwards governed durable-memory APIs without touching runtime-memory methods.
- `aworld-cli/src/aworld_cli/memory/relevance.py`
  Reads only active governed durable records when scoring promoted durable content.
- `aworld-cli/src/aworld_cli/builtin_plugins/memory_cli/hooks/task_completed.py`
  Writes session-log candidates, governed decisions, and mode-aware durable promotions.
- `aworld-cli/src/aworld_cli/builtin_plugins/memory_cli/commands/memory.py`
  Adds governance status, promotions listing, and accept / reject / revert actions.
- `tests/cli_memory/test_metrics.py`
  Verifies quality metrics and rollout-threshold readiness.
- `tests/cli_memory/test_durable_memory.py`
  Verifies provider APIs for active durable reads and review-driven state.
- `tests/cli_memory/test_memory_acceptance.py`
  Locks `/remember` bypass and active recall behavior under `shadow` and `governed`.
- `tests/plugins/test_plugin_hooks.py`
  Verifies `task_completed` hook behavior for `off`, `shadow`, and `governed`.
- `tests/plugins/test_plugin_commands.py`
  Verifies `/memory status` and `/memory promotions ...` surfaces.
- `openspec/changes/2026-05-07-governed-session-log-promotion/tasks.md`
  Marks completed tasks as implementation lands.

### Do not modify as part of this plan

- `aworld/memory/main.py`
- `aworld/core/memory.py`
- `aworld/core/context/amni/prompt/neurons/aworld_file_neuron.py`
- `AworldMemory` summary / history / long-term extraction behavior
- trajectory or `llm_calls` contracts

Those files are outside the allowed implementation surface frozen by the spec.

---

### Task 1: Add Governed Decision Models And Append-Only Stores

**Files:**
- Create: `aworld-cli/src/aworld_cli/memory/governance.py`
- Create: `tests/cli_memory/test_governance.py`
- Modify: `aworld-cli/src/aworld_cli/memory/promotion.py`

- [ ] **Step 1: Write the failing governance tests**

```python
# tests/cli_memory/test_governance.py
import json

from aworld_cli.memory.governance import (
    append_governed_decision,
    append_governed_review,
    evaluate_governed_candidate,
    governance_mode,
    list_governed_decisions,
)


def test_governance_mode_defaults_to_shadow(monkeypatch):
    monkeypatch.delenv("AWORLD_CLI_PROMOTION_MODE", raising=False)
    assert governance_mode() == "shadow"


def test_evaluate_governed_candidate_blocks_temporary_content(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    decision = evaluate_governed_candidate(
        workspace_path=workspace,
        candidate={
            "candidate_id": "cand-1",
            "content": "Temporary debug note for the current task only.",
            "memory_type": "workspace",
            "confidence": "low",
            "source_ref": {"session_id": "s1", "task_id": "t1", "candidate_id": "cand-1"},
        },
        mode="governed",
    )

    assert decision.decision == "rejected"
    assert "temporary_candidate" in decision.blockers


def test_append_governed_decision_and_review_are_append_only(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    append_governed_decision(
        workspace,
        {
            "decision_id": "dec-1",
            "decision": "session_log_only",
            "policy_mode": "shadow",
            "source_ref": {"session_id": "s1", "task_id": "t1", "candidate_id": "cand-1"},
        },
    )
    append_governed_review(
        workspace,
        {
            "decision_id": "dec-1",
            "review_action": "confirmed",
        },
    )

    decisions = list_governed_decisions(workspace)
    assert decisions[0]["decision_id"] == "dec-1"
    assert decisions[0]["reviews"][-1]["review_action"] == "confirmed"
```

- [ ] **Step 2: Run the focused governance tests to verify the feature is missing**

Run: `pytest tests/cli_memory/test_governance.py -q`

Expected: FAIL with `ModuleNotFoundError: No module named 'aworld_cli.memory.governance'`.

- [ ] **Step 3: Implement governed decision and review storage**

```python
# aworld-cli/src/aworld_cli/memory/governance.py
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from aworld_cli.memory.durable import INSTRUCTION_MEMORY_TYPES, read_durable_memory_records


@dataclass(frozen=True)
class GovernedDecision:
    decision_id: str
    candidate_id: str
    decision: str
    policy_mode: str
    policy_version: str
    reason: str
    blockers: tuple[str, ...] = ()
    confidence: str = ""
    memory_type: str = "workspace"
    content: str = ""
    source_ref: dict = field(default_factory=dict)
    evaluated_at: str = ""

    def to_payload(self) -> dict:
        return asdict(self)


def governance_mode() -> str:
    raw = os.getenv("AWORLD_CLI_PROMOTION_MODE", "shadow").strip().lower()
    return raw if raw in {"off", "shadow", "governed"} else "shadow"


def decisions_file(workspace_path: str | Path) -> Path:
    workspace = Path(workspace_path).expanduser().resolve()
    return workspace / ".aworld" / "memory" / "metrics" / "promotion_decisions.jsonl"


def reviews_file(workspace_path: str | Path) -> Path:
    workspace = Path(workspace_path).expanduser().resolve()
    return workspace / ".aworld" / "memory" / "metrics" / "promotion_reviews.jsonl"


def evaluate_governed_candidate(workspace_path: str | Path, candidate: dict, mode: str | None = None) -> GovernedDecision:
    mode = mode or governance_mode()
    content = str(candidate.get("content") or "").strip()
    memory_type = str(candidate.get("memory_type") or "workspace").strip().lower()
    blockers: list[str] = []
    if "temporary" in content.lower() or "current task" in content.lower():
        blockers.append("temporary_candidate")
    if memory_type not in INSTRUCTION_MEMORY_TYPES:
        blockers.append("ineligible_memory_type")
    if any(record.content == content for record in read_durable_memory_records(workspace_path, memory_type=memory_type)):
        blockers.append("duplicate_active_durable_memory")

    if blockers:
        decision = "rejected"
        reason = blockers[0]
    elif mode == "off":
        decision = "session_log_only"
        reason = "governance_mode_off"
    elif mode == "shadow":
        decision = "session_log_only"
        reason = "shadow_mode_no_auto_promotion"
    else:
        decision = "durable_memory"
        reason = "governed_policy_pass"

    return GovernedDecision(
        decision_id=f"gdec_{uuid4().hex[:12]}",
        candidate_id=str(candidate.get("candidate_id") or uuid4().hex[:12]),
        decision=decision,
        policy_mode=mode,
        policy_version="2026-05-07",
        reason=reason,
        blockers=tuple(blockers),
        confidence=str(candidate.get("confidence") or ""),
        memory_type=memory_type,
        content=content,
        source_ref=dict(candidate.get("source_ref") or {}),
        evaluated_at=datetime.now(timezone.utc).isoformat(),
    )


def append_governed_decision(workspace_path: str | Path, payload: dict) -> Path:
    target = decisions_file(workspace_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False))
        handle.write("\n")
    return target


def append_governed_review(workspace_path: str | Path, payload: dict) -> Path:
    target = reviews_file(workspace_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False))
        handle.write("\n")
    return target


def list_governed_decisions(workspace_path: str | Path) -> list[dict]:
    decision_lines = decisions_file(workspace_path).read_text(encoding="utf-8").splitlines() if decisions_file(workspace_path).exists() else []
    review_lines = reviews_file(workspace_path).read_text(encoding="utf-8").splitlines() if reviews_file(workspace_path).exists() else []
    reviews_by_decision: dict[str, list[dict]] = {}
    for line in review_lines:
        payload = json.loads(line)
        reviews_by_decision.setdefault(str(payload.get("decision_id")), []).append(payload)
    merged: list[dict] = []
    for line in decision_lines:
        payload = json.loads(line)
        payload["reviews"] = reviews_by_decision.get(str(payload.get("decision_id")), [])
        merged.append(payload)
    return merged
```

- [ ] **Step 4: Run the governance tests to verify the new store works**

Run: `pytest tests/cli_memory/test_governance.py -q`

Expected: PASS

- [ ] **Step 5: Commit the governance storage baseline**

```bash
git add tests/cli_memory/test_governance.py aworld-cli/src/aworld_cli/memory/governance.py aworld-cli/src/aworld_cli/memory/promotion.py
git commit -m "feat: add governed promotion decision store"
```

### Task 2: Route Hook And Provider Through Governed Promotion Modes

**Files:**
- Modify: `aworld-cli/src/aworld_cli/memory/provider.py`
- Modify: `aworld-cli/src/aworld_cli/memory/hybrid.py`
- Modify: `aworld-cli/src/aworld_cli/builtin_plugins/memory_cli/hooks/task_completed.py`
- Modify: `tests/cli_memory/test_durable_memory.py`
- Modify: `tests/plugins/test_plugin_hooks.py`
- Modify: `tests/cli_memory/test_memory_acceptance.py`

- [ ] **Step 1: Write the failing integration tests for `shadow` and `governed`**

```python
# tests/plugins/test_plugin_hooks.py
@pytest.mark.asyncio
async def test_memory_plugin_task_completed_hook_records_shadow_decision_without_durable_write(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True)
    monkeypatch.setenv("AWORLD_CLI_PROMOTION_MODE", "shadow")

    plugin = discover_plugins([_get_builtin_memory_plugin_root()])[0]
    hooks = load_plugin_hooks([plugin])

    await hooks["task_completed"][0].run(
        event={
            "session_id": "session-1",
            "task_id": "task-1",
            "workspace_path": str(workspace),
            "task_status": "idle",
            "final_answer": "Always use pnpm for workspace package management and never run npm install here.",
        },
        state={"workspace_path": str(workspace)},
    )

    assert not (workspace / ".aworld" / "memory" / "durable.jsonl").exists()
    decisions = (workspace / ".aworld" / "memory" / "metrics" / "promotion_decisions.jsonl").read_text(encoding="utf-8")
    assert "shadow_mode_no_auto_promotion" in decisions


@pytest.mark.asyncio
async def test_memory_plugin_task_completed_hook_governed_mode_writes_durable_memory(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True)
    monkeypatch.setenv("AWORLD_CLI_PROMOTION_MODE", "governed")

    plugin = discover_plugins([_get_builtin_memory_plugin_root()])[0]
    hooks = load_plugin_hooks([plugin])

    await hooks["task_completed"][0].run(
        event={
            "session_id": "session-1",
            "task_id": "task-1",
            "workspace_path": str(workspace),
            "task_status": "idle",
            "final_answer": "Always use pnpm for workspace package management and never run npm install here.",
        },
        state={"workspace_path": str(workspace)},
    )

    durable_payload = json.loads((workspace / ".aworld" / "memory" / "durable.jsonl").read_text(encoding="utf-8").strip())
    assert durable_payload["source"] == "governed_auto_promotion"
    assert durable_payload["decision_id"].startswith("gdec_")
```

```python
# tests/cli_memory/test_durable_memory.py
def test_provider_active_durable_records_exclude_reverted_promotions(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True)
    provider = CliDurableMemoryProvider()

    provider.append_durable_memory_record(
        workspace_path=workspace,
        text="Use pnpm for workspace package management",
        memory_type="workspace",
        source="governed_auto_promotion",
    )
    provider.record_governed_review(
        workspace_path=workspace,
        decision_id="gdec_123",
        review_action="reverted",
    )

    assert provider.get_active_durable_memory_records(workspace) == ()
```

- [ ] **Step 2: Run the focused provider and hook tests to verify the APIs are missing**

Run: `pytest tests/cli_memory/test_durable_memory.py tests/plugins/test_plugin_hooks.py tests/cli_memory/test_memory_acceptance.py -q`

Expected: FAIL because provider methods like `get_active_durable_memory_records()` and governed hook writes do not exist yet.

- [ ] **Step 3: Implement provider and hook integration**

```python
# aworld-cli/src/aworld_cli/memory/provider.py
from aworld_cli.memory.governance import (
    append_governed_review,
    list_governed_decisions,
)


# add these methods inside the existing CliDurableMemoryProvider class
def list_governed_decisions(self, workspace_path: str | Path) -> tuple[dict, ...]:
    return tuple(list_governed_decisions(workspace_path))


def record_governed_review(
    self,
    workspace_path: str | Path,
    *,
    decision_id: str,
    review_action: str,
) -> Path:
    return append_governed_review(
        workspace_path,
        {"decision_id": decision_id, "review_action": review_action},
    )


def get_active_durable_memory_records(
    self,
    workspace_path: str | Path,
    memory_type: str | None = None,
) -> tuple[DurableMemoryRecord, ...]:
    records = self.get_durable_memory_records(workspace_path, memory_type=memory_type)
    decisions = {
        item["decision_id"]: item
        for item in self.list_governed_decisions(workspace_path)
    }
    active: list[DurableMemoryRecord] = []
    for record in records:
        decision_id = getattr(record, "decision_id", None)
        reviews = decisions.get(decision_id, {}).get("reviews", [])
        if any(review.get("review_action") == "reverted" for review in reviews):
            continue
        active.append(record)
    return tuple(active)
```

```python
# aworld-cli/src/aworld_cli/builtin_plugins/memory_cli/hooks/task_completed.py
from aworld_cli.memory.governance import append_governed_decision, evaluate_governed_candidate


provider = CliDurableMemoryProvider()
if final_answer:
    extracted = evaluate_turn_end_candidate(final_answer)
    candidate_id = f"{session_id}:{event.get('task_id')}:{len(candidates)}"
    candidate = {
        "candidate_id": candidate_id,
        "content": extracted.content,
        "memory_type": extracted.memory_type,
        "confidence": extracted.confidence,
        "source_ref": {
            "session_id": session_id,
            "task_id": event.get("task_id"),
            "candidate_id": candidate_id,
        },
    }
    governed = evaluate_governed_candidate(
        workspace_path=workspace_path,
        candidate=candidate,
    )
    append_governed_decision(workspace_path, governed.to_payload())
    candidates.append(
        candidate_payload(
            extracted,
            auto_promoted=governed.decision == "durable_memory",
        )
        | {
            "candidate_id": candidate_id,
            "governed_decision_id": governed.decision_id,
            "governed_decision": governed.decision,
            "governed_reason": governed.reason,
            "governed_blockers": list(governed.blockers),
        }
    )
    if governed.decision == "durable_memory":
        provider.append_durable_memory_record(
            workspace_path=workspace_path,
            text=governed.content,
            memory_type=governed.memory_type,
            source="governed_auto_promotion",
        )
```

```python
# aworld-cli/src/aworld_cli/memory/hybrid.py
# add these methods inside the existing HybridMemoryProvider class
def list_governed_decisions(self, workspace_path: str | Path):
    return self.durable_provider.list_governed_decisions(workspace_path)


def get_active_durable_memory_records(
    self,
    workspace_path: str | Path,
    memory_type: str | None = None,
):
    return self.durable_provider.get_active_durable_memory_records(
        workspace_path,
        memory_type=memory_type,
    )
```

- [ ] **Step 4: Run the focused integration tests to verify mode-aware behavior**

Run: `pytest tests/cli_memory/test_durable_memory.py tests/plugins/test_plugin_hooks.py tests/cli_memory/test_memory_acceptance.py -q`

Expected: PASS

- [ ] **Step 5: Commit the provider and hook integration**

```bash
git add aworld-cli/src/aworld_cli/memory/provider.py aworld-cli/src/aworld_cli/memory/hybrid.py aworld-cli/src/aworld_cli/builtin_plugins/memory_cli/hooks/task_completed.py tests/cli_memory/test_durable_memory.py tests/plugins/test_plugin_hooks.py tests/cli_memory/test_memory_acceptance.py
git commit -m "feat: wire governed promotion through provider and hook"
```

### Task 3: Add Review Actions And `/memory promotions` Command Surface

**Files:**
- Modify: `aworld-cli/src/aworld_cli/builtin_plugins/memory_cli/commands/memory.py`
- Modify: `tests/plugins/test_plugin_commands.py`

- [ ] **Step 1: Write the failing command tests**

```python
# tests/plugins/test_plugin_commands.py
@pytest.mark.asyncio
async def test_memory_plugin_promotions_lists_governed_decisions(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True)
    (workspace / ".aworld" / "memory" / "metrics").mkdir(parents=True)
    (workspace / ".aworld" / "memory" / "metrics" / "promotion_decisions.jsonl").write_text(
        '{"decision_id":"gdec_1","decision":"session_log_only","policy_mode":"shadow","reason":"shadow_mode_no_auto_promotion","content":"Use pnpm for workspace package management","source_ref":{"session_id":"s1","task_id":"t1","candidate_id":"c1"}}\n',
        encoding="utf-8",
    )

    plugin = discover_plugins([_get_builtin_memory_plugin_root()])[0]
    snapshot = CommandRegistry.snapshot()
    try:
        CommandRegistry.clear()
        register_plugin_commands([plugin])
        command = CommandRegistry.get("memory")
        result = await command.execute(CommandContext(cwd=str(workspace), user_args="promotions"))
        assert "Governed promotions" in result
        assert "gdec_1" in result
        assert "shadow_mode_no_auto_promotion" in result
    finally:
        CommandRegistry.restore(snapshot)


@pytest.mark.asyncio
async def test_memory_plugin_promotions_reject_records_review(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True)
    (workspace / ".aworld" / "memory" / "metrics").mkdir(parents=True)
    (workspace / ".aworld" / "memory" / "metrics" / "promotion_decisions.jsonl").write_text(
        '{"decision_id":"gdec_1","decision":"session_log_only","policy_mode":"shadow","reason":"shadow_mode_no_auto_promotion","content":"Use pnpm","source_ref":{"session_id":"s1","task_id":"t1","candidate_id":"c1"}}\n',
        encoding="utf-8",
    )

    plugin = discover_plugins([_get_builtin_memory_plugin_root()])[0]
    snapshot = CommandRegistry.snapshot()
    try:
        CommandRegistry.clear()
        register_plugin_commands([plugin])
        command = CommandRegistry.get("memory")
        result = await command.execute(CommandContext(cwd=str(workspace), user_args="promotions reject gdec_1"))
        assert "Recorded review action: declined" in result
    finally:
        CommandRegistry.restore(snapshot)
```

- [ ] **Step 2: Run the focused command tests to verify the new subcommands are missing**

Run: `pytest tests/plugins/test_plugin_commands.py -q`

Expected: FAIL because `/memory promotions` parsing and review actions do not exist.

- [ ] **Step 3: Implement the command surface**

```python
# aworld-cli/src/aworld_cli/builtin_plugins/memory_cli/commands/memory.py
# extend the existing MemoryCommand class with these concrete changes
def completion_items(self) -> dict[str, str]:
    return {
        "/memory view": "View effective workspace memory instructions",
        "/memory reload": "Explain current memory reload behavior",
        "/memory status": "Show workspace memory status",
        "/memory cache": "Summarize request-linked cache observability from session logs",
        "/memory promotions": "List governed promotion decisions",
        "/memory promotions accept <decision-id>": "Confirm and promote a shadow candidate",
        "/memory promotions reject <decision-id>": "Record a declined review label",
        "/memory promotions revert <decision-id>": "Disable a previously promoted governed record",
    }


def _promotions_workspace_memory(
    self,
    context: CommandContext,
    *,
    action: str | None = None,
    decision_id: str | None = None,
) -> str:
    provider = self._provider()
    if action in {"accept", "reject", "revert"} and decision_id:
        review_action = {
            "accept": "confirmed",
            "reject": "declined",
            "revert": "reverted",
        }[action]
        provider.record_governed_review(
            context.cwd,
            decision_id=decision_id,
            review_action=review_action,
        )
        return f"Recorded review action: {review_action} for {decision_id}"

    decisions = provider.list_governed_decisions(context.cwd)
    lines = ["Governed promotions"]
    for item in decisions[-10:]:
        lines.append(
            f"- {item['decision_id']} {item['decision']} "
            f"[{item['policy_mode']}] {item['reason']}"
        )
    return "\n".join(lines)


# update execute() to dispatch the new subcommand
if subcommand == "promotions":
    action = tokens[1] if len(tokens) > 1 else None
    decision_id = tokens[2] if len(tokens) > 2 else None
    return self._promotions_workspace_memory(
        context,
        action=action,
        decision_id=decision_id,
    )
```

- [ ] **Step 4: Run the command tests to verify listing and review actions**

Run: `pytest tests/plugins/test_plugin_commands.py -q`

Expected: PASS

- [ ] **Step 5: Commit the command surface**

```bash
git add aworld-cli/src/aworld_cli/builtin_plugins/memory_cli/commands/memory.py tests/plugins/test_plugin_commands.py
git commit -m "feat: add governed promotion command surface"
```

### Task 4: Extend Metrics, Threshold Readiness, And Runtime Regression Coverage

**Files:**
- Modify: `aworld-cli/src/aworld_cli/memory/metrics.py`
- Modify: `aworld-cli/src/aworld_cli/memory/relevance.py`
- Modify: `tests/cli_memory/test_metrics.py`
- Modify: `tests/cli_memory/test_memory_acceptance.py`
- Modify: `openspec/changes/2026-05-07-governed-session-log-promotion/tasks.md`

- [ ] **Step 1: Write the failing metrics and regression tests**

```python
# tests/cli_memory/test_metrics.py
from aworld_cli.memory.governance import append_governed_decision, append_governed_review
from aworld_cli.memory.metrics import summarize_promotion_metrics


def test_promotion_metrics_summary_reports_quality_and_threshold_readiness(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True)
    append_governed_decision(
        workspace,
        {
            "decision_id": "gdec_1",
            "decision": "durable_memory",
            "policy_mode": "governed",
            "reason": "governed_policy_pass",
            "source_ref": {"session_id": "s1", "task_id": "t1", "candidate_id": "c1"},
        },
    )
    append_governed_review(workspace, {"decision_id": "gdec_1", "review_action": "confirmed"})

    summary = summarize_promotion_metrics(workspace)

    assert summary.reviewed_promotions == 1
    assert summary.confirmed_promotions == 1
    assert summary.reverted_promotions == 0
    assert summary.precision_proxy == 1.0
    assert summary.pollution_proxy == 0.0
    assert summary.default_rollout_ready is False
```

```python
# tests/cli_memory/test_memory_acceptance.py
def test_hybrid_runtime_message_memory_contract_is_unchanged(tmp_path):
    memory = _build_hybrid_memory(tmp_path)
    assert hasattr(memory, "get_last_n")
    assert hasattr(memory, "search")
```

- [ ] **Step 2: Run the focused metrics and regression tests to verify the new summary fields are missing**

Run: `pytest tests/cli_memory/test_metrics.py tests/cli_memory/test_memory_acceptance.py -q`

Expected: FAIL because `PromotionMetricsSummary` does not expose review-based quality fields yet.

- [ ] **Step 3: Implement governed quality metrics and threshold readiness**

```python
# aworld-cli/src/aworld_cli/memory/metrics.py
@dataclass(frozen=True)
class PromotionMetricsSummary:
    metrics_path: Path
    total_evaluations: int
    eligible_for_auto_promotion: int
    by_confidence: dict[str, int]
    by_promotion: dict[str, int]
    by_reason: dict[str, int]
    latest_decision: dict | None = None
    last_auto_promoted: dict | None = None
    last_eligible_blocked: dict | None = None
    reviewed_promotions: int = 0
    confirmed_promotions: int = 0
    reverted_promotions: int = 0
    pending_review: int = 0
    precision_proxy: float = 0.0
    pollution_proxy: float = 0.0
    default_rollout_ready: bool = False


def summarize_promotion_metrics(
    workspace_path: str | os.PathLike[str],
    *,
    max_records: int = 500,
):
    decisions = list_governed_decisions(workspace_path)
    reviewed_promotions = 0
    confirmed_promotions = 0
    reverted_promotions = 0
    pending_review = 0
    for decision in decisions:
        reviews = decision.get("reviews", [])
        if not reviews:
            pending_review += 1
            continue
        reviewed_promotions += 1
        if any(item.get("review_action") == "confirmed" for item in reviews):
            confirmed_promotions += 1
        if any(item.get("review_action") == "reverted" for item in reviews):
            reverted_promotions += 1
    precision_proxy = confirmed_promotions / reviewed_promotions if reviewed_promotions else 0.0
    pollution_proxy = reverted_promotions / reviewed_promotions if reviewed_promotions else 0.0
    default_rollout_ready = (
        reviewed_promotions >= 100
        and precision_proxy >= 0.90
        and pollution_proxy <= 0.05
    )
```

```python
# aworld-cli/src/aworld_cli/memory/relevance.py
PROMOTION_BONUS = {
    "durable_memory": 200,
    "session_log_only": 0,
    "rejected": -200,
}
```

- [ ] **Step 4: Run the metrics and regression suite**

Run: `pytest tests/cli_memory/test_metrics.py tests/cli_memory/test_memory_acceptance.py tests/cli_memory/test_governance.py tests/cli_memory/test_durable_memory.py tests/plugins/test_plugin_hooks.py tests/plugins/test_plugin_commands.py -q`

Expected: PASS

- [ ] **Step 5: Mark the OpenSpec tasks that are now complete and commit**

```bash
git add aworld-cli/src/aworld_cli/memory/metrics.py aworld-cli/src/aworld_cli/memory/relevance.py tests/cli_memory/test_metrics.py tests/cli_memory/test_memory_acceptance.py openspec/changes/2026-05-07-governed-session-log-promotion/tasks.md
git commit -m "test: add governed promotion metrics and regression coverage"
```

## Self-Review

### Spec coverage

- Source identity, mode control, and explainable decisions are covered by Task 1 and Task 2.
- Review / correction surfaces are covered by Task 3.
- Quality metrics, rollout thresholds, and runtime regression coverage are covered by Task 4.
- The allowed / discouraged / forbidden boundary is preserved because no task changes `aworld/core/memory.py`, `AworldMemory`, or amni prompt assembly internals.

### Placeholder scan

- No `TODO`, `TBD`, or “implement later” placeholders remain.
- Every task includes exact files, test commands, expected failures, and commit messages.

### Type consistency

- The plan uses one governed API vocabulary consistently:
  - `governance_mode`
  - `GovernedDecision`
  - `append_governed_decision`
  - `append_governed_review`
  - `list_governed_decisions`
  - `record_governed_review`
  - `get_active_durable_memory_records`
