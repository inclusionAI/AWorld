# Top-Level Skill Command Plugin Design

## Goal

Remove the hardcoded `skill` top-level CLI branch from `aworld-cli/src/aworld_cli/main.py` and replace it with a plugin-style top-level command architecture, while preserving the user-facing `aworld-cli skill ...` workflow and extending interactive skill discovery so installed skills can be manually invoked by skill-name slash aliases such as `/brainstorming`.

## Scope

This design covers:

- introducing a top-level CLI command plugin SPI for `aworld-cli <command>`
- migrating `skill` to a builtin top-level command provider
- keeping existing `aworld-cli skill install/list/enable/disable/remove/update/import` behavior stable
- extending `/skills` to show slash commands contributed by the same provider as visible skills
- generating standalone manual slash aliases from visible skill names
- establishing provider capability rules so future command additions do not require new `main.py` dispatch branches

This design does not cover:

- converting every existing top-level command to the new SPI in the same change
- making `skill` an uninstallable external plugin in this phase
- merging top-level CLI command execution with interactive slash command execution into one runtime abstraction
- introducing the long-term framework-owned skill model under `aworld/skills/`
- redesigning the skill runtime ABI, `skill_configs`, or `ContextSkillTool`
- introducing marketplace, trust, or version-resolution policy

This spec is intentionally limited to CLI extensibility. The framework-owned skill architecture is covered separately in [2026-04-22-framework-owned-skill-model-design.md](/Users/wuman/Documents/workspace/aworld-mas/aworld/docs/superpowers/specs/2026-04-22-framework-owned-skill-model-design.md).

## Problem Statement

The branch already moved installed skills onto a plugin-backed internal model, but one important framework boundary is still hardcoded:

- `aworld-cli/src/aworld_cli/main.py` still directly recognizes `skill`
- top-level command parsing and dispatch are still special-cased
- new first-class CLI features still tend to require edits in `main.py`

This creates two long-term problems:

1. the plugin model is not yet the default extension mechanism for CLI entrypoints
2. the interactive `/skills` surface knows about skill selection, but it does not present provider-contributed slash commands as part of the same discovery story

The target architecture should make these statements true:

- `skill` remains a stable user-facing product command
- `main.py` becomes bootstrap and dispatch infrastructure, not a growing list of feature branches
- future top-level commands can be added through a command SPI instead of one-off parser branches
- `/skills` remains the discovery and explicit-selection surface for skills, while also exposing related plugin commands and generated skill-name aliases without taking over their execution path

## Design Principles

1. Framework once, features many times
   - Add one reusable top-level command framework instead of one more hardcoded branch.

2. Preserve user muscle memory
   - Existing `aworld-cli skill ...` syntax and behavior remain intact.

3. Keep execution models separate
   - Top-level CLI commands and interactive slash commands are related capabilities, but they have different lifecycle and parsing needs.

4. Make provider ownership explicit
   - Skill and command association should come from shared provider ownership, not name heuristics.

5. Minimize first migration scope
   - Migrate `skill` first, prove the SPI, then expand command coverage later.

## Design Overview

This design introduces a new top-level command SPI, referred to here as `cli_commands`.

The architecture has three layers:

1. `main.py` bootstrap layer
   - Reads argv early
   - Initializes the top-level command registry
   - Registers builtin top-level command providers
   - Loads enabled plugin-provided top-level commands
   - Delegates parse and execution to the resolved command provider

2. Provider capability model
   - A provider may contribute `skills`, `slash_commands`, and `cli_commands`
   - The same provider may expose one, two, or all three capability types
   - `/skills` uses provider ownership to display related slash commands next to visible skills
   - visible skill names also generate manual slash aliases for explicit invocation

3. Execution surfaces
   - `aworld-cli <command>` uses the new top-level command SPI
   - `/command` continues to use the existing interactive `CommandRegistry`
   - `/skills` stays a discovery and explicit-selection UI, not a command router
   - generated `/skill-name` aliases act as shortcuts for explicit skill selection

## Top-Level Command SPI

### Core Abstraction

Introduce a dedicated interface for top-level commands. The exact class names may vary, but the framework should provide the following concepts:

- `TopLevelCommand`
  - `name`
  - `aliases`
  - `description`
  - `register_parser(subparsers_or_builder_context)`
  - `run(args, context) -> int | None`
  - optional metadata such as `requires_model_config`

- `TopLevelCommandRegistry`
  - register
  - unregister
  - get
  - list
  - snapshot/restore for tests if needed

- `TopLevelCommandContext`
  - cwd
  - raw argv if needed
  - loaded config access
  - plugin manager access
  - runtime factories or shared service handles needed by command handlers

This SPI is intentionally separate from the existing slash command abstraction in `aworld_cli/core/command_system.py`.

### Why Separate From Slash Commands

Interactive slash commands and top-level CLI commands differ in key ways:

- top-level commands parse process argv before interactive runtime exists
- top-level commands often return process exit codes
- slash commands are session-scoped and prompt-toolkit integrated
- slash commands can be prompt-mediated, whereas top-level commands are deterministic CLI entrypoints

Trying to force both through one abstraction in this phase would overcomplicate the migration and blur responsibilities.

## Bootstrap And Dispatch Flow

`aworld-cli/src/aworld_cli/main.py` should shrink to a predictable bootstrap flow:

1. create a minimal bootstrap parser only for very early global flags if truly required
2. initialize the top-level command registry
3. register builtin command providers, including `skill`
4. load enabled plugin-provided `cli_commands`
5. build the composite argparse surface from registered top-level commands
6. parse argv once through the assembled parser
7. dispatch execution to the selected top-level command
8. continue into interactive/direct mode only when no explicit top-level command was selected

The key design rule is that `main.py` no longer contains `if minimal_args.command == "skill"`.

## Builtin Skill Command Provider

`skill` is the first migrated top-level command and is implemented as a builtin provider.

### Why Builtin First

This phase does not make `skill` an externally removable plugin because that introduces bootstrap and supportability issues too early:

- the CLI needs `skill` before arbitrary external plugins are loaded or trusted
- `skill` is part of the expected baseline product workflow
- moving to builtin provider form already achieves the main architectural goal of eliminating hardcoded dispatch

### Behavior Contract

The builtin `skill` provider must preserve current behavior for:

- `install`
- `list`
- `enable`
- `disable`
- `remove`
- `update`
- `import`

The implementation may be moved out of `main.py`, but command syntax, output intent, and installed-state semantics should remain compatible with the current branch.

## Provider Capability Model

This design extends the capability view established in the previous skill/plugin unification work.

Each provider may contribute any combination of:

- `skills`
- `slash_commands`
- `cli_commands`

Provider ownership is the source of truth for relating capabilities.

### Examples

- a skill package with only `skills`
  - appears in `aworld-cli skill list`
  - appears in `/skills`
  - contributes a generated manual slash alias for each visible skill name

- a plugin with `skills` and `slash_commands`
  - appears in `aworld-cli skill list`
  - appears in `/skills`
  - exposes its slash commands globally through `CommandRegistry`
  - also contributes generated manual slash aliases for each visible skill name
  - shows those commands as related commands in `/skills`

- a plugin with only `slash_commands`
  - does not appear in `/skills` main skill list
  - still exposes its slash commands globally

- a builtin command provider with only `cli_commands`
  - appears in top-level CLI help
  - does not automatically appear in `/skills`

## `/skills` Discovery Model

`/skills` remains a skill discovery and explicit-selection command.

It does not become a nested command dispatcher such as `/skills review`.

### Supported Actions

The supported interactive actions remain:

- `/skills`
- `/skills use <name>`
- `/skills clear`
- generated `/skill-name` aliases for visible skills

### Extended Listing Behavior

When rendering `/skills`, the CLI should:

1. resolve the visible skills for the current runtime and agent
2. identify the provider that owns each visible skill
3. derive the generated manual slash alias for each visible skill name
4. gather visible slash commands contributed by that same provider
5. render skill rows plus related slash command metadata

The command association should be additive discovery metadata. It must not change how provider-contributed slash commands are executed.

### Execution Rules

Provider-contributed slash commands remain global commands such as `/review` or `/browser`.

They must continue to:

- be registered in the interactive `CommandRegistry`
- appear in prompt completions as standalone slash commands
- execute directly from their existing entrypoint

Generated skill-name aliases such as `/brainstorming` are different:

- they are derived from visible skill names, not from plugin `commands` entrypoints
- they remain standalone slash commands in completion
- they act as manual explicit-selection shortcuts, equivalent to `/skills use <skill-name>`
- they do not require the provider to declare a separate command entrypoint for that skill

`/skills` helps users discover both provider-contributed commands and generated skill-name aliases.

## Association Rules Between Skills And Slash Commands

Association must be explicit and provider-based.

The recommended rules are:

1. Every visible skill gets one generated alias in the form `/<skill-name>`.
2. If a provider contributes visible skills and visible slash commands, `/skills` may display those commands alongside those skills.
3. Provider-contributed command association is by shared provider identity, not by matching names, descriptions, or folders.
4. Hidden slash commands remain hidden in `/skills`.
5. Providers that contribute slash commands but no skills do not appear in `/skills` just because they have commands.
6. A visible skill with no provider-contributed slash commands still renders with its generated `/<skill-name>` alias.

This keeps `/skills` stable and avoids confusing cross-provider command attribution.

## Help, Completion, And Conflict Rules

### Top-Level CLI Help

`aworld-cli --help` must include builtin and plugin-provided top-level commands from the new registry.

### Interactive Completion

Interactive completion continues to enumerate:

- builtin interactive commands such as `/skills`
- registered slash commands from `CommandRegistry`
- generated `/<skill-name>` aliases for visible skills

No new `/skills <command>` completion namespace is added in this phase.

### Reserved Names And Conflicts

The framework should define reserved top-level names for baseline product commands.

Rules:

- builtin top-level commands win over plugin-provided commands
- plugin-provided top-level commands may not overwrite reserved names
- duplicate registrations should fail fast with a clear error or be skipped with structured logging, but the rule must be deterministic

`skill` is reserved in this phase.

## Migration Plan

### Phase 1: Framework Introduction

- add top-level command SPI and registry
- register builtin `skill` provider through the new registry
- remove the `skill` hardcoded branch from `main.py`
- keep all other top-level command handling unchanged unless minor refactoring is needed to fit the new bootstrap structure

### Phase 2: Interactive Discovery Enhancement

- extend `/skills` rendering to include generated `/<skill-name>` aliases and provider-related slash commands
- ensure interactive completion still comes from standalone slash command registration plus generated skill aliases
- add tests for visibility and association behavior

### Phase 3: Optional Follow-On Migrations

- migrate `plugins`, `gateway`, `batch`, or other top-level commands into `cli_commands`
- evaluate whether shared registry utilities should be generalized further
- do not externalize builtin `skill` provider until bootstrap and product support concerns are explicitly revisited

## Testing Strategy

Automated coverage should include:

1. top-level command registry behavior
   - register
   - conflict handling
   - builtin priority

2. `skill` provider dispatch
   - `aworld-cli skill ...` routes through the new provider
   - existing subcommands preserve behavior

3. help surface
   - `aworld-cli --help` includes registry-provided `skill`

4. `/skills` rendering
   - shows visible skills
   - shows generated `/<skill-name>` aliases
   - includes related visible slash commands from the same provider
   - excludes commands from providers with no visible skills

5. interactive completion
   - related slash commands remain independently completable
   - generated `/<skill-name>` aliases are completable
   - `/skills` completions remain limited to discovery and selection actions

6. regression coverage
   - existing skill install/list/enable/disable tests remain green
   - existing plugin command registration tests remain green
   - installing `https://github.com/obra/superpowers` yields visible skills and generated aliases such as `/brainstorming`

## Risks And Mitigations

### Risk: Bootstrap Complexity Leaks Into Command Providers

If command providers require too much runtime state at registration time, bootstrap becomes fragile.

Mitigation:

- keep registration metadata lightweight
- defer heavy service initialization until command execution where possible

### Risk: Confusing The User With Two Command Systems

Users may misread provider-contributed commands and generated skill aliases as the same kind of command.

Mitigation:

- render provider-contributed commands separately from generated `/<skill-name>` aliases
- keep help text explicit that generated aliases are manual skill-selection shortcuts

### Risk: Provider Attribution Is Incomplete For Legacy Skill Sources

Some compatibility-installed skill sources may not yet expose rich provider metadata.

Mitigation:

- require a stable provider identity for installed skill records
- degrade gracefully by omitting related command display when attribution is unavailable

### Risk: Scope Expansion During Migration

Turning this into a generic all-command migration would slow delivery and increase merge risk.

Mitigation:

- migrate only `skill` in this phase
- leave broader command pluginization as follow-on work

## Acceptance Criteria

The design is considered implemented when all of the following are true:

1. `aworld-cli/src/aworld_cli/main.py` no longer hardcodes a dedicated `skill` command branch.
2. `aworld-cli skill ...` is dispatched through a builtin top-level command provider.
3. Existing `skill` lifecycle subcommands remain behaviorally compatible.
4. `aworld-cli --help` shows `skill` via the new top-level command registry.
5. `aworld-cli skill install https://github.com/obra/superpowers` installs the repository as a usable skill package when the source shape is otherwise valid.
6. A subsequent interactive session discovers the installed visible skills automatically and shows them in `/skills`.
7. Each visible skill gets a generated standalone slash alias such as `/brainstorming`.
8. Generated skill aliases remain executable as manual selection shortcuts without requiring a plugin-declared command entrypoint.
9. Provider-contributed slash commands remain executable as standalone global slash commands.
10. Plugins that contribute commands but no skills do not appear in `/skills` solely because they expose commands.
11. Reserved-name conflict rules prevent external providers from overriding builtin `skill`.
