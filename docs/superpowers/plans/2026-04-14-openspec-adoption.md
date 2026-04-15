# OpenSpec Adoption Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the repository's ad hoc `docs/superpowers/*` change-management flow with OpenSpec, establish baseline capability specs, and move assistant workflow rules into `AGENTS.md`.

**Architecture:** Use a single migration change, `adopt-openspec`, as the bootstrap record. Archive that change into `openspec/specs/` so the repo ends with OpenSpec as the source of truth, while keeping legacy docs as historical references only.

**Tech Stack:** OpenSpec CLI, Markdown, existing repository documentation

---

### Task 1: Create The Migration Skeleton

**Files:**
- Create: `openspec/changes/adopt-openspec/.openspec.yaml`
- Create: `openspec/changes/adopt-openspec/proposal.md`
- Create: `openspec/changes/adopt-openspec/design.md`
- Create: `openspec/changes/adopt-openspec/tasks.md`

- [ ] **Step 1: Initialize OpenSpec in the repository**

Run:
```bash
openspec init --tools codex .
openspec new change adopt-openspec
```

Expected: `openspec/` exists and `openspec/changes/adopt-openspec/.openspec.yaml` is created.

- [ ] **Step 2: Write the migration artifacts**

Add a proposal, design, and task list that document the switch away from `docs/superpowers/*` and into OpenSpec.

### Task 2: Define Baseline Capability Specs

**Files:**
- Create: `openspec/changes/adopt-openspec/specs/change-governance/spec.md`
- Create: `openspec/changes/adopt-openspec/specs/agent-runtime/spec.md`
- Create: `openspec/changes/adopt-openspec/specs/workflow-hooks/spec.md`
- Create: `openspec/changes/adopt-openspec/specs/cli-experience/spec.md`
- Create: `openspec/changes/adopt-openspec/specs/skills-system/spec.md`
- Create: `openspec/changes/adopt-openspec/specs/training-integration/spec.md`

- [ ] **Step 1: Capture repository governance requirements**

Define that future behavior changes are proposed in `openspec/changes/` and the stable source of truth lives in `openspec/specs/`.

- [ ] **Step 2: Capture baseline product capabilities**

Define minimal but durable requirements for runtime, hooks, CLI, skills, and training so future changes have a starting contract.

### Task 3: Update Repository Entry Points

**Files:**
- Create: `AGENTS.md`
- Modify: `CLAUDE.md`
- Modify: `README.md`
- Create: `docs/superpowers/README.md`

- [ ] **Step 1: Move assistant workflow guidance into `AGENTS.md`**

Document repo-specific collaboration rules, validation expectations, and the new OpenSpec workflow.

- [ ] **Step 2: Retire the old entry points**

Replace `CLAUDE.md` with a short pointer to `AGENTS.md` and mark `docs/superpowers/` as historical-only.

### Task 4: Materialize Main Specs And Validate

**Files:**
- Modify: `openspec/specs/change-governance/spec.md`
- Modify: `openspec/specs/agent-runtime/spec.md`
- Modify: `openspec/specs/workflow-hooks/spec.md`
- Modify: `openspec/specs/cli-experience/spec.md`
- Modify: `openspec/specs/skills-system/spec.md`
- Modify: `openspec/specs/training-integration/spec.md`

- [ ] **Step 1: Validate and archive the migration change**

Run:
```bash
openspec validate adopt-openspec
openspec archive adopt-openspec -y
```

Expected: the change moves into `openspec/changes/archive/` and the six main specs are created under `openspec/specs/`.

- [ ] **Step 2: Replace generated placeholder purposes**

Edit the archived main specs so each `## Purpose` describes the capability instead of keeping the archive-generated placeholder text.

- [ ] **Step 3: Re-run repository validation**

Run:
```bash
openspec validate --specs
openspec list --specs
git status --short
```

Expected: all specs validate and the working tree only shows the intended migration files.
