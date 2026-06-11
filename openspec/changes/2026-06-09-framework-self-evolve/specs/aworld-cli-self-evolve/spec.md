## ADDED Requirements

### Requirement: aworld-cli MUST expose a command to invoke framework self-evolve

`aworld-cli` MUST provide exactly one phase-1 user-facing manual/debug command
that invokes the framework self-evolve capability for a specified target, task,
dataset, or previous run source. The command MUST reuse the same framework path
as asynchronous post-run self-evolve jobs, and it MUST extend behavior through
options and `--target <type>:<id>` forms rather than separate target-specific
commands. CLI MUST NOT own scheduler, evaluator, optimizer, target inference,
durable artifacts, or agent opt-in semantics.

#### Scenario: Optimize command is registered

- **WHEN** `aworld-cli` discovers built-in CLI commands
- **THEN** the optimize command MUST be exposed through the existing
  `builtin_plugins/*_cli/.aworld-plugin/plugin.json` and `cli_commands/`
  entrypoint pattern
- **AND** the implementation MUST NOT rely on
  `register_builtin_top_level_commands` as the registration mechanism

#### Scenario: User optimizes a skill target from a dataset

- **WHEN** the user runs `aworld-cli optimize --target skill:<name> --dataset <path>`
- **THEN** CLI MUST construct a framework self-evolve run for that skill target
- **AND** it MUST print the run id, report path, and selected candidate summary
- **AND** core optimization logic MUST execute in `aworld` framework APIs

#### Scenario: User optimizes a specified task

- **WHEN** the user runs `aworld-cli optimize --task <task>`
- **THEN** CLI MUST pass the task context to framework self-evolve
- **AND** framework self-evolve MUST run trajectory/target credit assignment
  when an explicit `--target` is not supplied
- **AND** CLI MUST NOT own the target inference logic

#### Scenario: User optimizes from prior session or trajectory

- **WHEN** the user provides `--from-session <session-id>` or
  `--from-trajectory <path>`
- **THEN** CLI MUST pass that source to framework dataset or diagnostic
  builders
- **AND** CLI MUST NOT parse trajectory semantics independently of framework
  APIs except for command argument validation
- **AND** the trajectory path MUST be optional user input, not a hard-coded
  product dependency

### Requirement: aworld-cli optimize MUST default to proposal-only application

CLI self-evolve runs MUST NOT write candidate changes by default. If a caller
requests `auto_verified`, CLI MUST pass the policy to framework APIs and MUST
NOT implement apply logic itself.

#### Scenario: User omits apply mode

- **WHEN** the user runs `aworld-cli optimize` without `--apply`
- **THEN** CLI MUST request framework proposal-only behavior
- **AND** it MUST clearly report that no target files were changed

#### Scenario: User requests write or branch application in phase 1

- **WHEN** the user passes `--apply write` or `--apply branch`
- **THEN** CLI MUST reject the request as unsupported in phase 1
- **AND** CLI MUST explain that phase 1 supports `proposal` by default and
  framework-gated `auto_verified` for allowlisted targets

#### Scenario: User requests verified automatic application

- **WHEN** the user passes `--apply auto_verified`
- **THEN** CLI MUST delegate the apply policy to framework self-evolve APIs
- **AND** framework gates MUST decide whether to apply, fall back to proposal, or
  reject the request
- **AND** a task-only or single-trajectory run without sufficient independent
  evaluation sources MUST report limited confidence instead of implying an
  automatic verified apply
- **AND** CLI MUST report the framework apply status from run artifacts

### Requirement: aworld-cli MUST NOT add separate self-evolve agent opt-in semantics

Agent opt-in MUST remain a framework `AgentConfig.self_evolve_config` concern.
CLI MAY pass normal framework configuration into `aworld-cli optimize`, but it
MUST NOT create a second CLI-owned self-evolve mode for the built-in AWorld main
agent.

#### Scenario: CLI builds or loads an agent for optimize

- **WHEN** `aworld-cli optimize` needs agent configuration
- **THEN** CLI MUST rely on framework agent configuration semantics
- **AND** CLI MUST NOT reinterpret self-evolve mode independently of framework
  config

#### Scenario: User wants post-run self-evolve behavior

- **WHEN** a user wants asynchronous post-run self-evolve
- **THEN** that behavior MUST be configured through framework agent config and
  framework scheduler APIs
- **AND** CLI MUST remain only a manual/debug invocation entrypoint

### Requirement: CLI self-evolve command MUST support first-version explicit target forms

CLI MUST provide stable first-version target syntax for skill text, prompt
sections, and tool descriptions. All target forms MUST be parsed by the same
generic `aworld-cli optimize` command.

#### Scenario: Target is a skill

- **WHEN** `--target skill:<name>` is provided
- **THEN** CLI MUST map it to the framework skill text target resolver

#### Scenario: Target is a prompt section

- **WHEN** `--target prompt:<section>` is provided
- **THEN** CLI MUST map it to the framework prompt section target resolver

#### Scenario: Target is a tool description

- **WHEN** `--target tool:<tool-name>` is provided
- **THEN** CLI MUST map it to the framework tool description target resolver

#### Scenario: Target is an agent config field in a later extension

- **WHEN** `--target agent-config:<field>` is provided
- **THEN** CLI MAY map it to the framework agent config target resolver
- **AND** if supported, framework gates MUST enforce field allowlisting

### Requirement: CLI self-evolve output MUST be actionable and auditable

CLI MUST surface enough information for users to inspect and continue a
self-evolve run.

#### Scenario: Optimize command completes

- **WHEN** a self-evolve CLI command completes
- **THEN** CLI MUST print the self-evolve run id
- **AND** it MUST print the report path
- **AND** it MUST print the selected or inferred target when available
- **AND** it MUST print the best candidate id if one exists
- **AND** it MUST print whether the run applied changes or only generated a
  proposal

#### Scenario: Optimize command fails

- **WHEN** a self-evolve CLI command fails after run creation
- **THEN** CLI MUST print the failed run id or report path if available
- **AND** framework artifacts MUST contain the failure reason

### Requirement: CLI phase 1 MUST NOT add additional self-evolve entrypoints

Phase 1 CLI exposure MUST stay limited to `aworld-cli optimize`.

#### Scenario: Interactive slash command is considered

- **WHEN** an interactive `/optimize` command is requested during phase 1
- **THEN** it MUST be deferred or treated as a later CLI UX change
- **AND** it MUST NOT become a second phase-1 entrypoint for self-evolve
