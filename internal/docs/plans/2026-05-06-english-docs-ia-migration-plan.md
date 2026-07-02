# English Docs IA Migration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restructure the English `docs/` tree into a clean five-section user documentation site, move internal documents out of `docs/`, consolidate CLI content under `docs/AWorld CLI/`, and keep the MkDocs build working.

**Architecture:** Treat the migration as a documentation IA refactor with three layers: user-facing docs kept in `docs/`, internal design/plan/history material relocated near the code or under `internal/docs/`, and site-build assets retained in place. Execute in small batches: navigation and skeleton first, then content moves/merges, then internal-doc relocation, then build verification and duplicate cleanup.

**Tech Stack:** Markdown, MkDocs, `docs/outline.py`, Git moves, repo-local shell tooling, existing AWorld code/docs content.

---

## File Structure Lock-In

### Final user-facing docs tree

```text
docs/
  Get Start/
  Agents/
    Runtime/
  AWorld CLI/
    Commands/
    Hooks/
      Examples/
    Plugins/
    Recipes/
  Environment/
  Training/
  css/
  imgs/
  js/
  outline.py
```

### Internal documentation destinations

```text
internal/docs/
  site/
  notes/
  pending-zh/
  plans/

aworld/internal/docs/
  design/
  designs/
  plans/
  notes/

aworld-cli/internal/docs/
  superpowers/
  notes/

aworld_gateway/internal/docs/
  plans/
  specs/
```

### Canonical page mapping

- `docs/Recipe/deep_search_recipe.md` -> `docs/AWorld CLI/Recipes/Deep Search.md`
- `docs/Recipe/miniapp_build_recipe.md` -> `docs/AWorld CLI/Recipes/Mini App Build.md`
- `docs/Recipe/video_create_recipe.md` -> `docs/AWorld CLI/Recipes/Video Creation.md`
- `docs/features/aworld-cli-memory.md` -> `docs/AWorld CLI/Commands/Memory.md`
- `docs/plugins/plugin-sdk.md` -> `docs/AWorld CLI/Plugins/Plugin SDK.md`
- `docs/plugins/ralph-session-loop-plugin.md` -> `docs/AWorld CLI/Plugins/Ralph Session Loop.md`
- `docs/features/ralph-runner-dual-mode.md` -> `docs/Agents/Runtime/Ralph Runner.md`
- `docs/Deployment/OceanBase.md` -> `docs/Environment/OceanBase.md`
- `docs/examples/hooks/README.md` and all example scripts -> `docs/AWorld CLI/Hooks/Examples/`
- `docs/features/parallel-subagent-spawning.md` -> `docs/Agents/Parallel Subagents.md`
- `docs/features/parallel-spawn-multi-agent-guide.md` -> `internal/docs/pending-zh/parallel-spawn-multi-agent-guide.md`

---

### Task 1: Create The New Docs Skeleton And Update Navigation Generation

**Files:**
- Modify: `docs/outline.py`
- Create: `docs/AWorld CLI/Overview.md`
- Create: `docs/AWorld CLI/Installation.md`
- Create: `docs/AWorld CLI/Configuration.md`
- Create: `docs/AWorld CLI/Commands/Overview.md`
- Create: `docs/AWorld CLI/Hooks/Overview.md`
- Create: `docs/AWorld CLI/Plugins/Overview.md`
- Create: `docs/AWorld CLI/Recipes/Overview.md`
- Verify: `.github/workflows/docs.yml`

- [ ] **Step 1: Write the failing docs-nav expectation note**

Record the intended top-level order in a scratch note before editing `outline.py`:

```text
Get Start
Agents
AWorld CLI
Environment
Training
```

- [ ] **Step 2: Update `docs/outline.py` top-level ordering and section priorities**

Change the section ordering and priorities to match the new IA:

```python
file_priority = {
    "Guides": ["Overview", "Quick Start", "Core Capabilities", "Parallel Tasks", "Streaming Response", "Hitl"],
    "Agents": ["Build Agent", "Build Multi-Agent System(Mas)", "Build Workflow", "Custom Agent", "Context", "Runtime", "Memory", "Trace", "Parallel Subagents"],
    "Runtime": ["Overview", "Custom Runner", "Hooks", "Ralph Runner"],
    "AWorld CLI": ["Overview", "Installation", "Configuration", "Commands", "Hooks", "Plugins", "Recipes"],
    "Commands": ["Overview", "Memory", "Cron", "Plugins", "Gateway", "Parallel Tasks"],
    "Hooks": ["Overview", "Examples"],
    "Plugins": ["Overview", "Plugin Sdk", "Ralph Session Loop"],
    "Recipes": ["Overview", "Deep Search", "Mini App Build", "Video Creation"],
    "Environment": ["Overview", "Using Api", "Env Client", "Advanced Capabilities", "OceanBase"],
    "Training": ["Trainer", "Trajectory", "Evaluation"],
}

dir_order = ["Get Start", "Agents", "AWorld CLI", "Environment", "Training"]
```

- [ ] **Step 3: Run outline generation to verify the nav file still builds**

Run: `python docs/outline.py`
Expected: creates `mkdocs.yml` and `index.md` without tracebacks

- [ ] **Step 4: Create minimal skeleton overview pages for the new CLI section**

Seed each new overview page with short user-facing content:

```md
# AWorld CLI

Use AWorld CLI to configure a workspace, run interactive sessions, use slash commands, extend behavior with hooks, manage plugins, and access workflow recipes.
```

Repeat this pattern for:

- `Commands/Overview.md`
- `Hooks/Overview.md`
- `Plugins/Overview.md`
- `Recipes/Overview.md`
- `Installation.md`
- `Configuration.md`

- [ ] **Step 5: Re-run outline generation and inspect nav placement**

Run: `python docs/outline.py`
Expected: `mkdocs.yml` contains `AWorld CLI` as the third top-level section

- [ ] **Step 6: Commit the navigation/skeleton batch**

```bash
git add docs/outline.py "docs/AWorld CLI"
git commit -m "docs: add aworld cli docs skeleton"
```

### Task 2: Migrate CLI Commands, Hooks, Plugins, And Recipes Into `AWorld CLI`

**Files:**
- Move/Create: `docs/AWorld CLI/Commands/Memory.md`
- Create: `docs/AWorld CLI/Commands/Cron.md`
- Create: `docs/AWorld CLI/Commands/Plugins.md`
- Create: `docs/AWorld CLI/Commands/Gateway.md`
- Create: `docs/AWorld CLI/Commands/Parallel Tasks.md`
- Move/Create: `docs/AWorld CLI/Plugins/Plugin SDK.md`
- Move/Create: `docs/AWorld CLI/Plugins/Ralph Session Loop.md`
- Move: `docs/AWorld CLI/Recipes/Deep Search.md`
- Move: `docs/AWorld CLI/Recipes/Mini App Build.md`
- Move: `docs/AWorld CLI/Recipes/Video Creation.md`
- Move: `docs/AWorld CLI/Hooks/Examples/README.md`
- Move: `docs/AWorld CLI/Hooks/Examples/*.sh`
- Move: `docs/AWorld CLI/Hooks/Examples/hooks.yaml.example`
- Modify: `README.md`
- Modify: `README_zh.md` only for broken English-doc links if necessary; otherwise leave for phase two

- [ ] **Step 1: Move recipe pages into the new recipes section**

Run:

```bash
mkdir -p "docs/AWorld CLI/Recipes"
mv docs/Recipe/deep_search_recipe.md "docs/AWorld CLI/Recipes/Deep Search.md"
mv docs/Recipe/miniapp_build_recipe.md "docs/AWorld CLI/Recipes/Mini App Build.md"
mv docs/Recipe/video_create_recipe.md "docs/AWorld CLI/Recipes/Video Creation.md"
```

Expected: `docs/Recipe/` becomes removable after this batch

- [ ] **Step 2: Move hooks examples into the user-facing CLI hooks section**

Run:

```bash
mkdir -p "docs/AWorld CLI/Hooks/Examples"
mv docs/examples/hooks/README.md "docs/AWorld CLI/Hooks/Examples/README.md"
mv docs/examples/hooks/*.sh "docs/AWorld CLI/Hooks/Examples/"
mv docs/examples/hooks/hooks.yaml.example "docs/AWorld CLI/Hooks/Examples/hooks.yaml.example"
```

Expected: all hook examples now live under CLI docs

- [ ] **Step 3: Create command reference pages from code-backed behavior**

Write pages using current implementation and tests as the source of truth:

- `Memory.md` from:
  - `docs/features/aworld-cli-memory.md`
  - `aworld-cli/src/aworld_cli/builtin_plugins/memory_cli/commands/memory.py`
- `Cron.md` from:
  - `aworld-cli/src/aworld_cli/commands/cron_cmd.py`
  - `tests/test_slash_commands.py`
- `Plugins.md` from:
  - slash command behavior
  - `plugin-sdk.md` usage subset
- `Gateway.md` from:
  - `aworld-cli/src/aworld_cli/top_level_commands/gateway_cmd.py`
  - `aworld-cli/src/aworld_cli/gateway_cli.py`
- `Parallel Tasks.md` from:
  - `docs/Get Start/Parallel Tasks.md`
  - CLI-dispatch/task behavior in current code

Use this page template:

```md
# Memory

## What It Does

## Commands

## Typical Workflow

## Notes And Limits
```

- [ ] **Step 4: Move plugin-specific user docs into the CLI plugins section**

Run:

```bash
mkdir -p "docs/AWorld CLI/Plugins"
mv docs/plugins/plugin-sdk.md "docs/AWorld CLI/Plugins/Plugin SDK.md"
mv docs/plugins/ralph-session-loop-plugin.md "docs/AWorld CLI/Plugins/Ralph Session Loop.md"
```

Then trim `Commands/Plugins.md` so it covers plugin usage and links to deeper plugin pages instead of duplicating SDK details.

- [ ] **Step 5: Update README links that still point to old docs paths**

Search for old doc paths and replace them with new canonical paths:

Run: `rg -n "docs/(Recipe|plugins|features|examples/hooks)" README.md README_zh.md`

Expected: either update links or explicitly leave Chinese-only references for phase two

- [ ] **Step 6: Run focused verification for moved files and broken links**

Run:

```bash
python docs/outline.py
rg -n "docs/(Recipe|plugins|features|examples/hooks)" README.md docs
```

Expected:

- outline generation succeeds
- old user-facing paths are either gone or only remain in internal/non-user content queued for migration

- [ ] **Step 7: Commit the CLI consolidation batch**

```bash
git add "docs/AWorld CLI" README.md README_zh.md
git commit -m "docs: consolidate aworld cli user docs"
```

### Task 3: Reclassify Framework And Environment Pages, And Remove Historical User-Doc Buckets

**Files:**
- Create: `docs/Agents/Parallel Subagents.md`
- Create: `docs/Agents/Runtime/Ralph Runner.md`
- Move/Create: `docs/Environment/OceanBase.md`
- Modify: `docs/Get Start/Parallel Tasks.md`
- Remove after migration: `docs/features/`
- Remove after migration: `docs/Deployment/`
- Remove after migration: `docs/Recipe/`
- Remove after migration: `docs/plugins/`
- Remove after migration: `docs/examples/`

- [ ] **Step 1: Re-home `RalphRunner` as framework runtime documentation**

Run:

```bash
mkdir -p "docs/Agents/Runtime"
mv docs/features/ralph-runner-dual-mode.md "docs/Agents/Runtime/Ralph Runner.md"
```

Then edit surrounding runtime pages to link to `Ralph Runner.md`.

- [ ] **Step 2: Re-home parallel subagent documentation as framework content**

Run:

```bash
mv docs/features/parallel-subagent-spawning.md "docs/Agents/Parallel Subagents.md"
mv docs/features/parallel-spawn-multi-agent-guide.md internal/docs/pending-zh/parallel-spawn-multi-agent-guide.md
```

Then add a short cross-link from `docs/Get Start/Parallel Tasks.md` to `docs/Agents/Parallel Subagents.md` so onboarding and deep-dive remain separate.

- [ ] **Step 3: Re-home OceanBase under environment docs**

Run:

```bash
mv docs/Deployment/OceanBase.md "docs/Environment/OceanBase.md"
```

Then edit the page title from `OceanBase Deployment Guide` to `OceanBase Setup` if the rest of the IA no longer uses `Deployment`.

- [ ] **Step 4: Remove empty historical user-doc directories**

Run:

```bash
rmdir docs/Deployment || true
rmdir docs/Recipe || true
rmdir docs/plugins || true
rmdir docs/examples/hooks || true
rmdir docs/examples || true
rmdir docs/features || true
```

Expected: these directories are either removed or left only if hidden files remain and need explicit follow-up

- [ ] **Step 5: Verify no historical user-doc buckets remain referenced by the generated nav**

Run:

```bash
python docs/outline.py
rg -n "Deployment|Recipe|plugins|features|examples" mkdocs.yml
```

Expected: no user-facing top-level nav entries for those legacy buckets

- [ ] **Step 6: Commit the framework/environment reclassification batch**

```bash
git add docs/Agents docs/Environment docs/Get\ Start internal/docs/pending-zh
git commit -m "docs: reclassify framework and environment pages"
```

### Task 4: Move Internal Designs, Plans, And One-Off Notes Out Of `docs/`

**Files:**
- Create: `aworld/internal/docs/design/`
- Create: `aworld/internal/docs/designs/`
- Create: `aworld/internal/docs/plans/`
- Create: `aworld/internal/docs/notes/`
- Create: `aworld-cli/internal/docs/superpowers/`
- Create: `aworld-cli/internal/docs/notes/`
- Create: `aworld_gateway/internal/docs/plans/`
- Create: `aworld_gateway/internal/docs/specs/`
- Create: `internal/docs/site/`
- Move: `docs/design/*`
- Move: `docs/designs/*`
- Move: `docs/plans/*`
- Move: `docs/superpowers/*`
- Move: `docs/DESIGN_SYSTEM.md`
- Move: selected one-off notes from `docs/*.md`

- [ ] **Step 1: Create internal destination directories**

Run:

```bash
mkdir -p aworld/internal/docs/{design,designs,plans,notes}
mkdir -p aworld-cli/internal/docs/{superpowers,notes}
mkdir -p aworld_gateway/internal/docs/{plans,specs}
mkdir -p internal/docs/{site,notes,pending-zh}
```

- [ ] **Step 2: Move framework-oriented design directories out of `docs/`**

Run:

```bash
mv docs/design/* aworld/internal/docs/design/
mv docs/designs/* aworld/internal/docs/designs/
mv docs/plans/* aworld/internal/docs/plans/
```

Expected: `docs/design`, `docs/designs`, and `docs/plans` become removable

- [ ] **Step 3: Move superpowers internal material under `aworld-cli`**

Run:

```bash
mv docs/superpowers/* aworld-cli/internal/docs/superpowers/
```

Then ensure gateway-specific files inside that subtree move one more step to `aworld_gateway/internal/docs/{plans,specs}` when their titles/topics are gateway-only.

- [ ] **Step 4: Move one-off notes to internal homes**

Run:

```bash
mv docs/DESIGN_SYSTEM.md internal/docs/site/DESIGN_SYSTEM.md
mv docs/aworld-agent-gap-analysis.md aworld/internal/docs/notes/aworld-agent-gap-analysis.md
mv docs/phase3-implementation-summary.md aworld/internal/docs/notes/phase3-implementation-summary.md
mv docs/compact-timeout-fix.md aworld-cli/internal/docs/notes/compact-timeout-fix.md
mv docs/file-path-display-improvement.md aworld-cli/internal/docs/notes/file-path-display-improvement.md
mv docs/slash-command-system-summary.md aworld-cli/internal/docs/notes/slash-command-system-summary.md
mv docs/tool-call-logging.md aworld-cli/internal/docs/notes/tool-call-logging.md
mv docs/tool-output-ux-improvements.md aworld-cli/internal/docs/notes/tool-output-ux-improvements.md
```

- [ ] **Step 5: Remove empty internal-content directories from `docs/`**

Run:

```bash
rmdir docs/design || true
rmdir docs/designs/hooks-v2 || true
rmdir docs/designs || true
rmdir docs/plans || true
rmdir docs/superpowers/plans || true
rmdir docs/superpowers/specs || true
rmdir docs/superpowers || true
```

- [ ] **Step 6: Verify that user docs no longer surface internal material**

Run:

```bash
python docs/outline.py
rg -n "superpowers|designs|plans|phase3|gap-analysis|tool-call-logging" mkdocs.yml docs/index.md
```

Expected: no internal-only topics appear in generated user navigation

- [ ] **Step 7: Commit the internal-doc relocation batch**

```bash
git add aworld/internal/docs aworld-cli/internal/docs aworld_gateway/internal/docs internal/docs docs
git commit -m "docs: move internal design material out of user docs"
```

### Task 5: Deduplicate Content And Normalize Cross-Links

**Files:**
- Modify: `docs/Get Start/*.md`
- Modify: `docs/Agents/*.md`
- Modify: `docs/Agents/Runtime/*.md`
- Modify: `docs/AWorld CLI/**/*.md`
- Verify: all Markdown links in `docs/`

- [ ] **Step 1: Normalize each topic to one canonical page**

Apply these rules:

- memory user operations -> `docs/AWorld CLI/Commands/Memory.md`
- plugin user operations -> `docs/AWorld CLI/Commands/Plugins.md`
- plugin developer/SDK details -> `docs/AWorld CLI/Plugins/Plugin SDK.md`
- Ralph CLI loop -> `docs/AWorld CLI/Plugins/Ralph Session Loop.md`
- Ralph framework runner -> `docs/Agents/Runtime/Ralph Runner.md`
- parallel-task onboarding -> `docs/Get Start/Parallel Tasks.md`
- parallel-subagent deep dive -> `docs/Agents/Parallel Subagents.md`

- [ ] **Step 2: Replace duplicated long-form explanations with short canonical links**

Use this edit pattern when two pages overlap:

```md
For command usage, see [Memory](../AWorld%20CLI/Commands/Memory.md).
For framework memory concepts, see [Memory](../Agents/Memory.md).
```

- [ ] **Step 3: Run a broken-link sweep across all user docs**

Run:

```bash
rg -n "\\]\\(([^)]+)\\)" docs -g'*.md'
```

Manually inspect any paths still pointing at:

- `docs/Recipe/`
- `docs/plugins/`
- `docs/features/`
- `docs/examples/hooks/`
- `docs/Deployment/`

- [ ] **Step 4: Run a duplicate-topic sweep**

Run:

```bash
rg -n "RalphRunner|/memory|/cron|spawn_parallel|OceanBase|hook" docs -g'*.md'
```

Expected: each major topic has a clear canonical page and only short cross-links elsewhere

- [ ] **Step 5: Commit the deduplication batch**

```bash
git add docs
git commit -m "docs: deduplicate user doc entry points"
```

### Task 6: Run Final Build Verification And Workspace Audit

**Files:**
- Verify: `docs/outline.py`
- Verify: generated `mkdocs.yml`
- Verify: generated `index.md`
- Verify: `.github/workflows/docs.yml`

- [ ] **Step 1: Generate the docs navigation and homepage**

Run: `python docs/outline.py`
Expected: `mkdocs.yml` and `index.md` regenerate successfully

- [ ] **Step 2: Run a local MkDocs build**

Run:

```bash
python -m pip install mkdocs mkdocs-material
mkdocs build
```

Expected: build completes without missing-file or nav errors

- [ ] **Step 3: Audit the top-level docs directory**

Run:

```bash
find docs -maxdepth 1 -type d | sort
```

Expected top-level user docs:

```text
docs
docs/AWorld CLI
docs/Agents
docs/Environment
docs/Get Start
docs/Training
docs/css
docs/imgs
docs/js
```

- [ ] **Step 4: Audit for forbidden user-doc buckets**

Run:

```bash
find docs -maxdepth 1 -type d | rg "Deployment|Recipe|plugins|features|examples|design|designs|plans|superpowers"
```

Expected: no matches

- [ ] **Step 5: Inspect git diff for accidental Chinese-doc changes**

Run:

```bash
git diff --name-only | rg "^docs_zh/"
```

Expected: no matches in this phase

- [ ] **Step 6: Commit the final verification/fixup batch**

```bash
git add docs mkdocs.yml index.md .github/workflows/docs.yml
git commit -m "docs: finalize english docs ia migration"
```

## Self-Review

- Spec coverage:
  - five top-level sections: covered by Task 1 and Task 6
  - CLI consolidation: covered by Task 2 and Task 5
  - hooks kept as user examples: covered by Task 2
  - deployment removed: covered by Task 3
  - internal docs moved out: covered by Task 4
  - English-only boundary: covered by Task 6
- Placeholder scan:
  - no `TBD`, `TODO`, or “implement later” language remains
  - each move destination is explicit
  - each verification step has exact commands
- Type/path consistency:
  - all new top-level targets use the same `docs/AWorld CLI/...` path family
  - internal targets are consistently grouped by `aworld/`, `aworld-cli/`, `aworld_gateway/`, and `internal/docs/`

## Execution Handoff

Plan complete and saved to `internal/docs/plans/2026-05-06-english-docs-ia-migration-plan.md`. Two execution options:

**1. Subagent-Driven (recommended)** - I dispatch a fresh subagent per task, review between tasks, fast iteration

**2. Inline Execution** - Execute tasks in this session using executing-plans, batch execution with checkpoints

**Which approach?**

