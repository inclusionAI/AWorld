# Typed Durable-Memory Taxonomy Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add additive `memory_kind` support to CLI durable memory so new records, governed decisions, recall, and command surfaces can distinguish instruction-eligible memory from recall-only memory without changing runtime message-memory semantics.

**Architecture:** Keep all taxonomy logic inside `aworld-cli` durable-memory modules and the memory plugin surface. `memory_type` remains the storage-routing field, while `memory_kind` becomes the semantic layer used for write compatibility, instruction mirroring, relevant recall, and governed promotion eligibility. Existing untyped records stay readable through compatibility helpers instead of migration rewrites.

**Tech Stack:** Python 3.10+, `pytest`, JSONL append-only storage, existing `aworld-cli` durable/provider/governance modules, memory plugin commands/hooks, hybrid memory provider seam.

---

## File Structure

### Modify

- `aworld-cli/src/aworld_cli/memory/durable.py`
  Add canonical kind constants, normalization helpers, compatibility helpers, and `memory_kind` persistence/reads for durable records.
- `aworld-cli/src/aworld_cli/memory/governance.py`
  Extend governed decisions with `memory_kind` and enforce kind-aware eligibility for auto-promotion.
- `aworld-cli/src/aworld_cli/memory/promotion.py`
  Infer a best-effort `memory_kind` for session-log candidates without changing legacy `memory_type` routing.
- `aworld-cli/src/aworld_cli/memory/relevance.py`
  Add kind-aware durable-memory recall helpers so typed durable records participate in relevant recall without being forced into instruction text.
- `aworld-cli/src/aworld_cli/memory/provider.py`
  Thread `memory_kind` through explicit writes, instruction mirroring, relevant recall, and durable/governed inspection APIs.
- `aworld-cli/src/aworld_cli/builtin_plugins/memory_cli/commands/remember.py`
  Accept `--kind` and keep legacy `/remember` usage working.
- `aworld-cli/src/aworld_cli/builtin_plugins/memory_cli/commands/memory.py`
  Show kind information in `/memory view`, `/memory status`, and `/memory promotions`.
- `aworld-cli/src/aworld_cli/builtin_plugins/memory_cli/hooks/task_completed.py`
  Persist candidate/decision `memory_kind` and let governed promotion honor typed eligibility.
- `tests/cli_memory/test_durable_memory.py`
  Cover normalization, compatibility reads, typed writes, and instruction-mirroring rules.
- `tests/cli_memory/test_governance.py`
  Cover kind-aware governed decisions and legacy fallback behavior.
- `tests/cli_memory/test_memory_acceptance.py`
  Cover relevant recall vs instruction injection and hybrid runtime regression behavior.
- `tests/plugins/test_plugin_commands.py`
  Cover `/remember --kind`, typed inspection surfaces, and typed promotion listings.
- `tests/plugins/test_plugin_hooks.py`
  Cover hook-produced typed governed decisions and recall-only gating.
- `openspec/changes/2026-05-07-typed-durable-memory-taxonomy/tasks.md`
  Mark completed OpenSpec tasks after implementation lands.

### Do not modify as part of this plan

- `aworld/core/memory.py`
- `aworld/memory/main.py`
- `aworld/dataset/trajectory_strategy.py`
- `aworld/models/*`
- runtime message-history / summary / `llm_calls` contracts

Those files are explicitly out of scope for this taxonomy upgrade.

---

### Task 1: Add Taxonomy Primitives And Compatibility Reads

**Files:**
- Modify: `tests/cli_memory/test_durable_memory.py`
- Modify: `aworld-cli/src/aworld_cli/memory/durable.py`
- Modify: `aworld-cli/src/aworld_cli/memory/provider.py`

- [ ] **Step 1: Write the failing durable-memory taxonomy tests**

```python
def test_append_durable_memory_record_persists_memory_kind(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True)

    result = append_durable_memory_record(
        workspace,
        memory_type="workspace",
        memory_kind="workflow",
        text="Use pnpm for workspace package management",
        source="remember_command",
    )

    records = read_all_durable_memory_records(workspace)

    assert result.record_created is True
    assert records[0].memory_type == "workspace"
    assert records[0].memory_kind == "workflow"


def test_read_all_durable_memory_records_preserves_legacy_untyped_entries(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True)
    durable_file = workspace / ".aworld" / "memory" / "durable.jsonl"
    durable_file.parent.mkdir(parents=True, exist_ok=True)
    durable_file.write_text(
        '{"recorded_at":"2026-05-08T00:00:00+00:00","memory_type":"workspace","content":"Use pnpm","source":"remember_command"}\n',
        encoding="utf-8",
    )

    records = read_all_durable_memory_records(workspace)

    assert len(records) == 1
    assert records[0].memory_type == "workspace"
    assert records[0].memory_kind is None


def test_normalize_memory_kind_rejects_unknown_values() -> None:
    with pytest.raises(ValueError, match="Invalid durable memory kind"):
        normalize_memory_kind("opinionated")
```

- [ ] **Step 2: Run the focused durable-memory tests to verify `memory_kind` support is missing**

Run: `pytest tests/cli_memory/test_durable_memory.py -q`

Expected: FAIL because `append_durable_memory_record()` does not accept `memory_kind`, `DurableMemoryRecord` has no `memory_kind`, and `normalize_memory_kind()` does not exist yet.

- [ ] **Step 3: Implement additive taxonomy support in the durable-memory layer**

```python
# aworld-cli/src/aworld_cli/memory/durable.py
DURABLE_MEMORY_KINDS = ("preference", "constraint", "workflow", "fact", "reference")
INSTRUCTION_ELIGIBLE_MEMORY_KINDS = frozenset({"preference", "constraint", "workflow"})
RECALL_ONLY_MEMORY_KINDS = frozenset({"fact", "reference"})


@dataclass(frozen=True)
class DurableMemoryRecord:
    memory_type: str
    content: str
    source: str
    recorded_at: str
    source_file: Path
    decision_id: str = ""
    source_ref: dict[str, str] | None = None
    memory_kind: str | None = None


def normalize_memory_kind(memory_kind: str | None) -> str | None:
    if memory_kind is None:
        return None
    normalized = memory_kind.strip().lower()
    if not normalized:
        return None
    if normalized in DURABLE_MEMORY_KINDS:
        return normalized
    valid = ", ".join(DURABLE_MEMORY_KINDS)
    raise ValueError(f"Invalid durable memory kind: {memory_kind}. Valid kinds: {valid}")


def is_instruction_eligible_kind(memory_kind: str | None, *, memory_type: str) -> bool:
    normalized_kind = normalize_memory_kind(memory_kind)
    if normalized_kind is not None:
        return normalized_kind in INSTRUCTION_ELIGIBLE_MEMORY_KINDS
    return memory_type in INSTRUCTION_MEMORY_TYPES
```

```python
# aworld-cli/src/aworld_cli/memory/durable.py inside read_all_durable_memory_records()
record_kind = normalize_memory_kind(payload.get("memory_kind")) if "memory_kind" in payload else None

records.append(
    DurableMemoryRecord(
        memory_type=record_type,
        memory_kind=record_kind,
        content=content.strip(),
        source=source if isinstance(source, str) and source.strip() else "unknown",
        recorded_at=recorded_at if isinstance(recorded_at, str) else "",
        source_file=target,
        decision_id=decision_id.strip() if isinstance(decision_id, str) else "",
        source_ref=_normalize_source_ref(source_ref),
    )
)
```

```python
# aworld-cli/src/aworld_cli/memory/durable.py inside append_durable_memory_record()
def append_durable_memory_record(
    workspace_path: str | os.PathLike[str],
    *,
    memory_type: str,
    text: str,
    source: str,
    memory_kind: str | None = None,
    decision_id: str | None = None,
    source_ref: dict[str, str] | None = None,
) -> DurableMemoryWriteResult:
    normalized_kind = normalize_memory_kind(memory_kind)
    payload = {
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "memory_type": normalized_type,
        "content": normalized_text,
        "source": source,
    }
    if normalized_kind is not None:
        payload["memory_kind"] = normalized_kind
```

```python
# aworld-cli/src/aworld_cli/memory/provider.py
def append_durable_memory_record(
    self,
    workspace_path: str | Path,
    *,
    text: str,
    memory_type: str,
    source: str,
    memory_kind: str | None = None,
    decision_id: str | None = None,
    source_ref: dict[str, str] | None = None,
) -> ExplicitDurableWriteResult:
    write_result = append_durable_memory_record(
        workspace_path=workspace_path,
        text=text,
        memory_type=memory_type,
        memory_kind=memory_kind,
        source=source,
        decision_id=decision_id,
        source_ref=source_ref,
    )
```

- [ ] **Step 4: Re-run the focused durable-memory tests**

Run: `pytest tests/cli_memory/test_durable_memory.py -q`

Expected: PASS for the new normalization/compatibility/typed-write cases.

- [ ] **Step 5: Commit the taxonomy primitives baseline**

```bash
git add tests/cli_memory/test_durable_memory.py aworld-cli/src/aworld_cli/memory/durable.py aworld-cli/src/aworld_cli/memory/provider.py
git commit -m "feat: add typed durable memory taxonomy baseline"
```

---

### Task 2: Make Instruction Injection And Relevant Recall Kind-Aware

**Files:**
- Modify: `tests/cli_memory/test_memory_acceptance.py`
- Modify: `tests/cli_memory/test_durable_memory.py`
- Modify: `aworld-cli/src/aworld_cli/memory/relevance.py`
- Modify: `aworld-cli/src/aworld_cli/memory/provider.py`

- [ ] **Step 1: Write the failing recall/injection tests**

```python
@pytest.mark.asyncio
async def test_fact_memory_is_recallable_but_not_mirrored_into_workspace_instructions(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True)

    provider = CliDurableMemoryProvider()
    result = provider.append_durable_memory_record(
        workspace,
        text="The release branch is cut from main every Thursday.",
        memory_type="workspace",
        memory_kind="fact",
        source="remember_command",
    )

    assert result.instruction_target is None
    assert not (workspace / ".aworld" / "AWORLD.md").exists()


@pytest.mark.asyncio
async def test_relevant_memory_context_includes_matching_typed_durable_fact(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True)

    provider = CliDurableMemoryProvider()
    provider.append_durable_memory_record(
        workspace,
        text="The release branch is cut from main every Thursday.",
        memory_type="workspace",
        memory_kind="fact",
        source="remember_command",
    )

    context = provider.get_relevant_memory_context(
        workspace,
        query="What branch do releases cut from on Thursday?",
    )

    assert "The release branch is cut from main every Thursday." in context.texts
```

- [ ] **Step 2: Run the focused recall/injection tests to verify current behavior is too coarse**

Run: `pytest tests/cli_memory/test_durable_memory.py tests/cli_memory/test_memory_acceptance.py -q`

Expected: FAIL because all `workspace` durable writes still mirror into `AWORLD.md`, and relevant recall only considers session-log text.

- [ ] **Step 3: Implement kind-aware instruction and recall behavior**

```python
# aworld-cli/src/aworld_cli/memory/relevance.py
from aworld_cli.memory.durable import read_durable_memory_records


def recall_relevant_durable_memory_texts(
    workspace_path: str | os.PathLike[str] | None,
    query: str,
    *,
    limit: int = 3,
) -> tuple[tuple[str, ...], tuple[Path, ...]]:
    query_tokens = _tokenize(query)
    if not query_tokens:
        return (), ()

    ranked: list[tuple[int, str, str, Path]] = []
    for record in read_durable_memory_records(workspace_path):
        score = _score_text(record.content, query_tokens)
        if score <= 0:
            continue
        ranked.append((score, record.recorded_at, record.content, record.source_file))

    ranked.sort(key=lambda item: (item[0], item[1]), reverse=True)
    selected = ranked[: max(limit, 0)]
    return tuple(item[2] for item in selected), tuple(dict.fromkeys(item[3] for item in selected))
```

```python
# aworld-cli/src/aworld_cli/memory/provider.py
from aworld_cli.memory.durable import is_instruction_eligible_kind
from aworld_cli.memory.relevance import (
    recall_relevant_durable_memory_texts,
    recall_relevant_session_log_texts,
)

def get_relevant_memory_context(
    self,
    workspace_path: str | Path | None = None,
    query: str = "",
    limit: int = 3,
) -> RelevantMemoryContext:
    durable_texts, durable_files = recall_relevant_durable_memory_texts(
        workspace_path=workspace_path,
        query=query,
        limit=limit,
    )
    session_texts, session_files = recall_relevant_session_log_texts(
        workspace_path=workspace_path,
        query=query,
        limit=limit,
    )
    merged_texts = tuple(dict.fromkeys((*durable_texts, *session_texts)))
    merged_files = tuple(dict.fromkeys((*durable_files, *session_files)))
    return RelevantMemoryContext(texts=merged_texts[:limit], source_files=merged_files)


if is_instruction_eligible_kind(memory_kind, memory_type=write_result.memory_type):
    instruction_target, instruction_updated = append_remembered_guidance(
        workspace_path=workspace_path,
        text=text,
    )
```

- [ ] **Step 4: Re-run the focused recall/injection tests**

Run: `pytest tests/cli_memory/test_durable_memory.py tests/cli_memory/test_memory_acceptance.py -q`

Expected: PASS with typed facts/references staying out of standing instruction text while still appearing in relevant recall when they match the query.

- [ ] **Step 5: Commit the kind-aware recall/injection slice**

```bash
git add tests/cli_memory/test_durable_memory.py tests/cli_memory/test_memory_acceptance.py aworld-cli/src/aworld_cli/memory/relevance.py aworld-cli/src/aworld_cli/memory/provider.py
git commit -m "feat: make typed durable memory recall and injection kind-aware"
```

---

### Task 3: Extend `/remember` And `/memory` Surfaces For Typed Memory

**Files:**
- Modify: `tests/plugins/test_plugin_commands.py`
- Modify: `aworld-cli/src/aworld_cli/builtin_plugins/memory_cli/commands/remember.py`
- Modify: `aworld-cli/src/aworld_cli/builtin_plugins/memory_cli/commands/memory.py`

- [ ] **Step 1: Write the failing command-surface tests**

```python
@pytest.mark.asyncio
async def test_remember_command_accepts_kind_flag(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True)

    plugin = discover_plugins([_get_builtin_memory_plugin_root()])[0]
    snapshot = CommandRegistry.snapshot()
    try:
        CommandRegistry.clear()
        register_plugin_commands([plugin])
        command = CommandRegistry.get("remember")

        result = await command.execute(
            CommandContext(
                cwd=str(workspace),
                user_args="--kind workflow Use pnpm for workspace package management",
            )
        )

        assert "workflow durable memory" in result
    finally:
        CommandRegistry.restore(snapshot)


@pytest.mark.asyncio
async def test_memory_plugin_status_reports_kind_counts(tmp_path, monkeypatch):
    home = tmp_path / "home"
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True)
    (home / ".aworld").mkdir(parents=True)
    (home / ".aworld" / "AWORLD.md").write_text("global rule", encoding="utf-8")
    (workspace / ".aworld").mkdir(parents=True)
    (workspace / ".aworld" / "AWORLD.md").write_text("workspace rule", encoding="utf-8")
    (workspace / ".aworld" / "memory").mkdir(parents=True)
    (workspace / ".aworld" / "memory" / "durable.jsonl").write_text(
        '{"recorded_at":"2026-05-08T00:00:00+00:00","memory_type":"workspace","memory_kind":"workflow","content":"Use pnpm","source":"remember_command"}\n'
        '{"recorded_at":"2026-05-08T00:01:00+00:00","memory_type":"workspace","memory_kind":"fact","content":"Release branch is cut from main","source":"remember_command"}\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(Path, "home", lambda: home)

    plugin = discover_plugins([_get_builtin_memory_plugin_root()])[0]
    snapshot = CommandRegistry.snapshot()
    try:
        CommandRegistry.clear()
        register_plugin_commands([plugin])
        command = CommandRegistry.get("memory")
        result = await command.execute(CommandContext(cwd=str(workspace), user_args="status"))
    finally:
        CommandRegistry.restore(snapshot)

    assert "Durable record kinds:" in result
    assert "- workflow: 1" in result
    assert "- fact: 1" in result


@pytest.mark.asyncio
async def test_memory_plugin_promotions_show_memory_kind(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True)
    append_governed_decision(
        workspace,
        {
            "decision_id": "gdec_123",
            "candidate_id": "cand-123",
            "decision": "session_log_only",
            "policy_mode": "shadow",
            "policy_version": "2026-05-07",
            "reason": "shadow_mode_no_auto_promotion",
            "blockers": [],
            "confidence": "high",
            "memory_type": "workspace",
            "memory_kind": "constraint",
            "content": "Never run npm install in this repo.",
            "source_ref": {
                "session_id": "session-1",
                "task_id": "task-1",
                "candidate_id": "cand-123",
            },
            "evaluated_at": "2026-05-08T00:00:00+00:00",
        },
    )

    plugin = discover_plugins([_get_builtin_memory_plugin_root()])[0]
    snapshot = CommandRegistry.snapshot()
    try:
        CommandRegistry.clear()
        register_plugin_commands([plugin])
        command = CommandRegistry.get("memory")
        result = await command.execute(CommandContext(cwd=str(workspace), user_args="promotions"))
    finally:
        CommandRegistry.restore(snapshot)

    assert "memory_kind=constraint" in result
```

- [ ] **Step 2: Run the focused command tests to verify typed UX is missing**

Run: `pytest tests/plugins/test_plugin_commands.py -q`

Expected: FAIL because `/remember` does not parse `--kind`, `/memory status` only counts `memory_type`, and `/memory promotions` does not render typed semantics.

- [ ] **Step 3: Implement narrow typed command support**

```python
# aworld-cli/src/aworld_cli/builtin_plugins/memory_cli/commands/remember.py
from aworld_cli.memory.durable import (
    DEFAULT_DURABLE_MEMORY_TYPE,
    DURABLE_MEMORY_KINDS,
    DURABLE_MEMORY_TYPES,
    normalize_durable_memory_type,
    normalize_memory_kind,
)


def _parse_remember_args(user_args: str) -> tuple[str, str | None, str] | str:
    memory_kind: str | None = None
    while index < len(tokens):
        token = tokens[index]
        if token == "--kind":
            if index + 1 >= len(tokens):
                return _usage()
            memory_kind = tokens[index + 1]
            index += 2
            continue
        elif token.startswith("--kind="):
            memory_kind = token.split("=", 1)[1]
            index += 1
            continue
        if token == "--type":
            if index + 1 >= len(tokens):
                return _usage()
            memory_type = tokens[index + 1]
            index += 2
            continue
        if token.startswith("--type="):
            memory_type = token.split("=", 1)[1]
            index += 1
            continue
        guidance_tokens.append(token)
        index += 1
    normalized_kind = normalize_memory_kind(memory_kind)
    return normalized_type, normalized_kind, guidance
```

```python
# aworld-cli/src/aworld_cli/builtin_plugins/memory_cli/commands/memory.py
if durable_records:
    lines.append("Durable record kinds:")
    kind_counts = Counter(record.memory_kind or "legacy_untyped" for record in durable_records)
    for memory_kind in sorted(kind_counts):
        lines.append(f"- {memory_kind}: {kind_counts[memory_kind]}")

for record in durable_records:
    kind_label = record.memory_kind or "legacy_untyped"
    lines.append(f"- [{record.memory_type}/{kind_label}] {record.content}")

lines.append(f"  memory_kind={item.get('memory_kind', 'legacy_untyped')}")
```

```python
# aworld-cli/src/aworld_cli/builtin_plugins/memory_cli/commands/remember.py
result = CliDurableMemoryProvider().append_durable_memory_record(
    context.cwd,
    text=guidance,
    memory_type=memory_type,
    memory_kind=memory_kind,
    source="remember_command",
)
```

- [ ] **Step 4: Re-run the focused command tests**

Run: `pytest tests/plugins/test_plugin_commands.py -q`

Expected: PASS for `/remember --kind`, typed status counts, typed view labels, and typed promotion listings while legacy commands still work unchanged.

- [ ] **Step 5: Commit the typed operator-surface changes**

```bash
git add tests/plugins/test_plugin_commands.py aworld-cli/src/aworld_cli/builtin_plugins/memory_cli/commands/remember.py aworld-cli/src/aworld_cli/builtin_plugins/memory_cli/commands/memory.py
git commit -m "feat: expose typed durable memory in memory commands"
```

---

### Task 4: Make Governed Promotion Kind-Aware And Verify The Full Slice

**Files:**
- Modify: `tests/cli_memory/test_governance.py`
- Modify: `tests/plugins/test_plugin_hooks.py`
- Modify: `tests/cli_memory/test_memory_acceptance.py`
- Modify: `aworld-cli/src/aworld_cli/memory/promotion.py`
- Modify: `aworld-cli/src/aworld_cli/memory/governance.py`
- Modify: `aworld-cli/src/aworld_cli/builtin_plugins/memory_cli/hooks/task_completed.py`
- Modify: `openspec/changes/2026-05-07-typed-durable-memory-taxonomy/tasks.md`

- [ ] **Step 1: Write the failing governed-promotion tests**

```python
def test_evaluate_governed_candidate_rejects_reference_kind_for_instructional_auto_promotion(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    decision = evaluate_governed_candidate(
        workspace_path=workspace,
        candidate={
            "candidate_id": "cand-1",
            "content": "See docs/release.md for the release checklist.",
            "memory_type": "reference",
            "memory_kind": "reference",
            "confidence": "high",
            "eligible_for_auto_promotion": True,
            "source_ref": {
                "session_id": "s1",
                "task_id": "t1",
                "candidate_id": "cand-1",
            },
        },
        mode="governed",
    )

    assert decision.decision != "durable_memory"
    assert "ineligible_memory_kind" in decision.blockers
```

```python
@pytest.mark.asyncio
async def test_task_completed_hook_persists_memory_kind_for_governed_candidates(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True)
    monkeypatch.setenv("AWORLD_CLI_PROMOTION_MODE", "governed")

    plugin = discover_plugins([_get_builtin_memory_plugin_root()])[0]
    hooks = load_plugin_hooks([plugin])

    await hooks["task_completed"][0].run(
        event={
            "session_id": "session-1",
            "task_id": "task-1",
            "task_status": "idle",
            "workspace_path": str(workspace),
            "final_answer": "Always use pnpm for workspace package management.",
        },
        state={"workspace_path": str(workspace)},
    )

    decisions = CliDurableMemoryProvider().list_governed_decisions(workspace)
    assert decisions[0]["memory_kind"] in {"workflow", "constraint", "preference"}
```

- [ ] **Step 2: Run the focused governance/hook tests to verify kind-aware eligibility is missing**

Run: `pytest tests/cli_memory/test_governance.py tests/plugins/test_plugin_hooks.py tests/cli_memory/test_memory_acceptance.py -q`

Expected: FAIL because candidates/decisions do not carry `memory_kind`, and reference/fact-like candidates are still governed only by coarse `memory_type`.

- [ ] **Step 3: Implement minimal `memory_kind` inference and governed gating**

```python
# aworld-cli/src/aworld_cli/memory/promotion.py
def infer_memory_kind(content: str, *, memory_type: str = "workspace") -> str | None:
    lowered = content.lower()
    if any(token in lowered for token in ("never ", "must ", "do not ", "don't ")):
        return "constraint"
    if any(token in lowered for token in ("always use ", "use ", "prefer ", "keep ")):
        return "workflow"
    if memory_type == "reference" or any(token in lowered for token in ("see ", "read ", ".md", "docs/", "file ")):
        return "reference"
    if any(token in lowered for token in ("branch", "release", "version", "owner", "deadline")):
        return "fact"
    return None


@dataclass(frozen=True)
class PromotionDecision:
    memory_type: str
    source: str
    content: str
    confidence: str
    promotion: str
    reason: str
    eligible_for_auto_promotion: bool
    evaluated_at: str
    memory_kind: str | None = None
```

```python
# aworld-cli/src/aworld_cli/memory/governance.py
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
    memory_kind: str | None = None
    content: str = ""
    source_ref: dict[str, str] = field(default_factory=dict)
    evaluated_at: str = ""
    memory_kind: str | None = None


if memory_kind is not None and memory_kind not in INSTRUCTION_ELIGIBLE_MEMORY_KINDS:
    blockers.append("ineligible_memory_kind")
```

```python
# aworld-cli/src/aworld_cli/builtin_plugins/memory_cli/hooks/task_completed.py
governed = evaluate_governed_candidate(
    workspace_path=workspace_path,
    candidate={
        "candidate_id": candidate_id,
        "content": str(persisted_candidate.get("content") or ""),
        "memory_type": str(persisted_candidate.get("memory_type") or decision.memory_type),
        "memory_kind": persisted_candidate.get("memory_kind"),
        "confidence": str(persisted_candidate.get("confidence") or ""),
        "eligible_for_auto_promotion": persisted_candidate.get(
            "eligible_for_auto_promotion"
        ),
        "source_ref": governed_source_ref,
    },
)

if auto_promoted:
    provider.append_durable_memory_record(
        workspace_path=workspace_path,
        text=governed.content,
        memory_type=governed.memory_type,
        memory_kind=governed.memory_kind,
        source="governed_auto_promotion",
        decision_id=governed.decision_id,
        source_ref=governed.source_ref,
    )
```

- [ ] **Step 4: Re-run the focused governance/hook tests**

Run: `pytest tests/cli_memory/test_governance.py tests/plugins/test_plugin_hooks.py tests/cli_memory/test_memory_acceptance.py -q`

Expected: PASS with governed decisions exposing `memory_kind`, instruction-eligible kinds auto-promoting safely, and recall-only kinds staying out of standing instruction behavior.

- [ ] **Step 5: Mark the OpenSpec tasks that are now complete**

Update `openspec/changes/2026-05-07-typed-durable-memory-taxonomy/tasks.md` to mark completed items as implementation lands.

- [ ] **Step 6: Run the full typed-taxonomy verification suite**

Run:

```bash
pytest -q tests/cli_memory/test_durable_memory.py tests/cli_memory/test_governance.py tests/cli_memory/test_memory_acceptance.py tests/plugins/test_plugin_commands.py tests/plugins/test_plugin_hooks.py
```

Expected: PASS with no failures.

- [ ] **Step 7: Commit the governed taxonomy integration**

```bash
git add tests/cli_memory/test_governance.py tests/plugins/test_plugin_hooks.py tests/cli_memory/test_memory_acceptance.py aworld-cli/src/aworld_cli/memory/promotion.py aworld-cli/src/aworld_cli/memory/governance.py aworld-cli/src/aworld_cli/builtin_plugins/memory_cli/hooks/task_completed.py openspec/changes/2026-05-07-typed-durable-memory-taxonomy/tasks.md
git commit -m "feat: make governed promotion respect typed durable memory"
```

---

## Spec Coverage Check

- Scope freeze: covered by the file boundary and the explicit “do not modify” list.
- Taxonomy model: Task 1 adds `memory_kind`, canonical kinds, compatibility reads, and stable `memory_type` routing.
- Kind-aware behavior: Task 2 and Task 4 add instruction-eligibility, recall behavior, and governed gating.
- Operator surface: Task 3 extends `/remember` and `/memory` inspection surfaces without breaking legacy usage.
- Validation: each task contains focused TDD steps, and Task 4 ends with the full verification suite plus OpenSpec task tracking.
