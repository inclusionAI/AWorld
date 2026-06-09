## ADDED Requirements

### Requirement: aworld-cli MUST expose a command to invoke framework self-evolve

`aworld-cli` MUST provide a user-facing command that invokes the framework
self-evolve capability for a specified target, task, dataset, or previous run
source.

#### Scenario: User optimizes a skill target from a dataset

- **WHEN** the user runs `aworld-cli optimize --target skill:<name> --dataset <path>`
- **THEN** CLI MUST construct a framework self-evolve run for that skill target
- **AND** it MUST print the run id, report path, and selected candidate summary
- **AND** core optimization logic MUST execute in `aworld` framework APIs

#### Scenario: User optimizes a specified task

- **WHEN** the user runs `aworld-cli optimize --task <task>`
- **THEN** CLI MUST pass the task context to framework self-evolve
- **AND** framework self-evolve MUST decide or require which target should be
  optimized according to its target selection policy

#### Scenario: User optimizes from prior session or trajectory

- **WHEN** the user provides `--from-session <session-id>` or
  `--from-trajectory <path>`
- **THEN** CLI MUST pass that source to framework dataset or diagnostic
  builders
- **AND** CLI MUST NOT parse trajectory semantics independently of framework
  APIs except for command argument validation

### Requirement: aworld-cli optimize MUST default to proposal-only application

CLI self-evolve runs MUST NOT write candidate changes by default.

#### Scenario: User omits apply mode

- **WHEN** the user runs `aworld-cli optimize` without `--apply`
- **THEN** CLI MUST request framework proposal-only behavior
- **AND** it MUST clearly report that no target files were changed

#### Scenario: User requests write or branch application

- **WHEN** the user passes `--apply write` or `--apply branch`
- **THEN** CLI MUST require the framework gates to pass before application
- **AND** CLI MUST report the applied candidate id and destination

### Requirement: Built-in AWorld main agent MUST support explicit self-evolve opt-in configuration

The built-in `Aworld` main agent in `aworld-cli` MUST support explicit
self-evolve opt-in through environment or config flags, while remaining off by
default.

#### Scenario: No self-evolve environment or config is set

- **WHEN** `aworld-cli` builds the default `Aworld` main agent
- **THEN** the agent MUST have self-evolve disabled by default
- **AND** existing task behavior MUST remain unchanged

#### Scenario: Self-evolve environment variables are set

- **WHEN** `AWORLD_AGENT_OPTIMIZE=1` or an equivalent approved config is set
- **THEN** CLI MAY construct the built-in `Aworld` agent with optimization
  eligibility enabled
- **AND** `AWORLD_SELF_EVOLVE_MODE` MAY select the configured mode

### Requirement: CLI self-evolve command MUST support explicit target forms

CLI MUST provide stable target syntax that maps to framework target types.

#### Scenario: Target is a skill

- **WHEN** `--target skill:<name>` is provided
- **THEN** CLI MUST map it to the framework skill text target resolver

#### Scenario: Target is a prompt section

- **WHEN** `--target prompt:<section>` is provided
- **THEN** CLI MUST map it to the framework prompt section target resolver

#### Scenario: Target is a tool description

- **WHEN** `--target tool:<tool-name>` is provided
- **THEN** CLI MUST map it to the framework tool description target resolver

#### Scenario: Target is an agent config field

- **WHEN** `--target agent-config:<field>` is provided
- **THEN** CLI MUST map it to the framework agent config target resolver
- **AND** framework gates MUST enforce field allowlisting

### Requirement: CLI self-evolve output MUST be actionable and auditable

CLI MUST surface enough information for users to inspect and continue a
self-evolve run.

#### Scenario: Optimize command completes

- **WHEN** a self-evolve CLI command completes
- **THEN** CLI MUST print the evolution run id
- **AND** it MUST print the report path
- **AND** it MUST print the best candidate id if one exists
- **AND** it MUST print whether the run applied changes or only generated a
  proposal

#### Scenario: Optimize command fails

- **WHEN** a self-evolve CLI command fails after run creation
- **THEN** CLI MUST print the failed run id or report path if available
- **AND** framework artifacts MUST contain the failure reason

### Requirement: Optional interactive `/optimize` MUST reuse the same framework command path

If interactive `/optimize` is added, it MUST call the same framework self-evolve
APIs as the top-level CLI command.

#### Scenario: User runs `/optimize last-task`

- **WHEN** interactive mode receives `/optimize last-task`
- **THEN** CLI MUST resolve the last task/session source
- **AND** it MUST invoke framework self-evolve with that source
- **AND** it MUST not implement a separate optimization algorithm
