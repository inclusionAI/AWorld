# English Docs Information Architecture Redesign

## Goal

Restructure the English user documentation under `docs/` so it serves as a clean user-facing documentation site rather than a mixed workspace for product docs, design explorations, implementation plans, and intermediate notes.

The redesign should:

- keep `docs/` focused on user documentation
- move internal design/plan/analysis material out of `docs/`
- consolidate CLI-related user content into a single `docs/AWorld CLI/` section
- reduce duplicate or competing entry points
- preserve the current MkDocs publishing flow

## Current Problems

The current `docs/` tree mixes several incompatible content types:

- user-facing product documentation
- internal designs and implementation plans
- one-off summaries and analysis notes
- site assets and build scripts
- examples that are useful to users but detached from the pages that explain them

This creates several user-facing problems:

1. The top-level information architecture is inconsistent.
2. CLI content is split across `features/`, `plugins/`, `Recipe/`, README links, and examples.
3. Internal documents occupy the same space as user docs and dilute navigation.
4. Multiple directories describe similar topics with different entry points.

## User-Facing Scope

The English user documentation should keep exactly these top-level sections:

```text
docs/
  Get Start/
  Agents/
  AWorld CLI/
  Environment/
  Training/
```

No `Deployment/`, `features/`, `plugins/`, `Recipe/`, `examples/`, `design/`, `designs/`, `plans/`, or `superpowers/` content directories should remain as top-level user-doc sections.

## Section Responsibilities

### Get Start

Entry point for first-time users.

Typical content:

- overview
- quick start
- core capabilities
- streaming response
- HITL
- parallel tasks only if the page is still framed as onboarding

### Agents

Framework-facing conceptual and capability documentation.

Typical content:

- build agent
- custom agent
- memory
- context
- workflow
- multi-agent system
- trace
- runtime overview
- custom runner

This section should explain framework concepts, not CLI operations.

### AWorld CLI

Single home for all CLI user documentation.

This section should absorb all user-facing CLI content currently scattered across historical directories.

Recommended structure:

```text
docs/AWorld CLI/
  Overview.md
  Installation.md
  Configuration.md
  Commands/
    Overview.md
    Memory.md
    Cron.md
    Plugins.md
    Gateway.md
    Parallel Tasks.md
  Hooks/
    Overview.md
    Examples/
      README.md
      audit_logger.sh
      dangerous_command_blocker.sh
      hooks.yaml.example
      output_filter.sh
      path_sanitizer.sh
      rate_limiter.sh
      session_notification.sh
  Recipes/
    Overview.md
    Deep Search.md
    Mini App Build.md
    Video Creation.md
```

Rules for this section:

- command documentation belongs under `Commands/`
- hooks are treated as user-facing CLI extension examples
- recipes are CLI-driven workflows, not a separate top-level product area
- plugin usage belongs here; plugin implementation SDK docs may be split if they are not end-user material

### Environment

Environment setup, model/API integration, client integration, and advanced runtime capabilities.

Only content that helps users configure or connect runtime dependencies should live here.

### Training

Trajectory, trainer, and evaluation documentation only.

This section should remain scoped to training/evaluation workflows.

## Mapping Rules

### Keep in place

- `docs/Get Start/*` stays under `Get Start/`
- `docs/Agents/*` stays under `Agents/`
- `docs/Environment/*` stays under `Environment/`
- `docs/Training/*` stays under `Training/`
- `docs/css/*`, `docs/js/*`, `docs/imgs/*`, and `docs/outline.py` remain as site assets/build support

### Move into `AWorld CLI`

- `docs/Recipe/*` -> `docs/AWorld CLI/Recipes/*`
- `docs/plugins/*` -> `docs/AWorld CLI/Plugins/*` or `docs/AWorld CLI/Commands/Plugins.md`, depending on whether content is command usage or broader plugin usage
- `docs/features/aworld-cli-memory.md` -> `docs/AWorld CLI/Commands/Memory.md`
- gateway usage pages -> `docs/AWorld CLI/Commands/Gateway.md`
- user-facing parallel task and subagent CLI operation content -> `docs/AWorld CLI/Commands/Parallel Tasks.md`
- `docs/examples/hooks/*` -> `docs/AWorld CLI/Hooks/Examples/*`

### Reclassify or split

- `docs/features/*` should not survive as a directory
- each page in `features/` must be classified as one of:
  - CLI user documentation -> move into `AWorld CLI`
  - framework capability documentation -> move into `Agents`
  - internal analysis or implementation-specific notes -> move out of `docs/`

- `docs/Deployment/OceanBase.md`
  - if it is truly user-facing environment/setup material, merge into `Environment`
  - otherwise move out of `docs/`

### Move out of `docs/`

The following content classes are internal and should not remain inside the user docs tree:

- `docs/design/*`
- `docs/designs/*`
- `docs/plans/*`
- `docs/superpowers/*`
- one-off implementation summaries and analysis notes such as:
  - `docs/aworld-agent-gap-analysis.md`
  - `docs/compact-timeout-fix.md`
  - `docs/file-path-display-improvement.md`
  - `docs/phase3-implementation-summary.md`
  - `docs/slash-command-system-summary.md`
  - `docs/tool-call-logging.md`
  - `docs/tool-output-ux-improvements.md`

These should be moved to internal directories located near the corresponding feature areas rather than kept under `docs/`.

## Internal Documentation Placement

Internal documentation should move out of `docs/` and live near the code or subsystem it describes.

Examples:

- gateway design/plan material near `aworld_gateway/`
- CLI command and plugin design/plan material near `aworld-cli/`
- framework architecture notes near `aworld/`
- docs-site-specific internal planning under `internal/docs/`

The exact destination can vary by topic, but the rule is:

> user documentation stays in `docs/`; implementation thinking stays with the implementation area.

## Migration Principles

1. Restructure first, rewrite second.
2. Prefer one canonical page per topic.
3. Remove duplicate entry points instead of preserving aliases everywhere.
4. Keep user examples, but only when they are attached to explanatory pages.
5. Do not keep historical directories just because files already exist there.
6. Avoid expanding scope into a full content rewrite of the entire site.

## MkDocs Constraints

The current docs site is built by:

- `.github/workflows/docs.yml`
- `docs/outline.py`

The redesign must preserve a valid `docs/` tree for the existing build flow, or update the outline generation logic in a controlled way if the new structure requires it.

The redesign should also update `docs/outline.py` so the generated navigation reflects the new top-level structure:

- `Get Start`
- `Agents`
- `AWorld CLI`
- `Environment`
- `Training`

Legacy assumptions such as `Deployment` in `dir_order` should be removed.

## Phase Boundary

This design only covers the English documentation tree.

`docs_zh/` is explicitly deferred to a second pass after the English information architecture is stable.

## Non-Goals

- full bilingual sync in the same change
- rewriting every existing page from scratch
- changing site branding/theme beyond what is needed for the IA move
- deleting historical internal documents without relocating them

## Acceptance Criteria

The redesign is complete when all of the following are true:

1. `docs/` top-level user content is limited to:
   - `Get Start`
   - `Agents`
   - `AWorld CLI`
   - `Environment`
   - `Training`
2. Internal designs, plans, and implementation summaries are no longer inside `docs/`.
3. CLI user documentation has one obvious home under `docs/AWorld CLI/`.
4. Hooks examples remain available to users under the CLI docs tree.
5. The generated docs navigation matches the new IA.
6. No major topic still exists in two competing user-facing locations.

