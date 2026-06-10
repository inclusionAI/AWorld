## MODIFIED Requirements

### Requirement: Source-backed evaluation inputs

Suite-backed evaluation flows SHALL support framework-owned input sources that normalize external evaluation records into cases and optional existing evaluator state.

#### Scenario: Source provides task and answer records
- **WHEN** an input source provides records with case id, task input, and an existing answer
- **THEN** the framework SHALL allow that source or an explicit state adapter to convert each record into evaluator state without re-executing an agent, task, or program

#### Scenario: Source metadata is reported safely
- **WHEN** source records include metadata
- **THEN** the framework SHALL preserve serializable source metadata and exclude live file handles, clients, process objects, and other runtime handles

#### Scenario: Source default adapter is available
- **WHEN** a source kind has one obvious replay adapter
- **THEN** the framework SHALL allow suite construction to use that default adapter without requiring the caller to pass both source and adapter explicitly

### Requirement: Source state adapters

Source-backed evaluation flows SHALL separate reading input records from converting existing outputs into evaluator state, while allowing sources to declare a default adapter for the common path.

#### Scenario: Answer adapter converts existing answer
- **WHEN** an answer state adapter receives a task+answer record
- **THEN** it SHALL produce an evaluator state with terminal answer, completion view, success status, source metadata, and no runtime execution

#### Scenario: Adapter fails on malformed state
- **WHEN** a source record claims to contain existing output state but required fields are malformed
- **THEN** the framework SHALL raise a clear validation error before judging or reporting the case

### Requirement: Replay harness for existing outputs

Suite-backed evaluation flows SHALL support replaying existing outputs through a runtime harness without re-executing the target.

#### Scenario: Replay harness returns adapted state
- **WHEN** a replay harness is configured with a source record and state adapter
- **THEN** it SHALL return the adapted `RolloutState` or bridgeable evaluator state as the case rollout result

#### Scenario: Replay is distinct from execution
- **WHEN** a source already contains answer, trajectory, or rollout state
- **THEN** the framework SHALL NOT invoke the suite's agent, task, or program execution adapter for that case unless explicitly configured to do so

#### Scenario: Replay state feeds existing scorers
- **WHEN** replayed state contains answer, outcome, trajectory, tool calls, usage, timing, or standard metrics
- **THEN** existing judge, trajectory, outcome, reward, standard metric, gate, and report paths SHALL consume that state through the same normalized evaluator interfaces used by runtime-composed execution

### Requirement: AWorld trajectory log source

The framework SHALL provide a source and adapter for trusted AWorld trajectory log records.

#### Scenario: Trajectory log source selects task ids
- **WHEN** a trajectory log source is configured with one or more task ids
- **THEN** it SHALL extract the matching line-oriented AWorld trajectory records and expose one source record per task id

#### Scenario: Trajectory log record is parsed
- **WHEN** a trajectory log record contains ANSI-decorated Python dict repr with a JSON-string `trajectory` field
- **THEN** the framework SHALL clean ANSI escapes, parse the record, decode the trajectory, and surface a structured record or a clear parse error

#### Scenario: Trajectory log adapter builds rollout state
- **WHEN** a trajectory log adapter receives a parsed trajectory record
- **THEN** it SHALL produce rollout state containing terminal answer, ordered trajectory steps, extracted tool calls, evidence summary, outcome metadata, usage/timing defaults, and standard metrics

### Requirement: Source suite factory remains syntax sugar

Framework helpers for source-backed evaluation SHALL construct ordinary suite-backed evaluation definitions and SHALL NOT introduce a parallel suite type.

#### Scenario: Source helper creates suite
- **WHEN** a caller uses `create_source_eval_suite` with a supported source, judge backend, judge schema, and gate policy
- **THEN** the helper SHALL return a normal `EvalSuiteDef` that can be passed to existing suite-backed flow execution

#### Scenario: Source helper uses default adapter
- **WHEN** a caller omits `state_adapter` and the source provides a default adapter
- **THEN** the helper SHALL use the source default adapter for replay construction

### Requirement: Markdown agent judge loading

Evaluator judge backends SHALL support loading trusted markdown agent definitions without requiring callers to create temporary skill directories.

#### Scenario: Judge backend loads agent markdown
- **WHEN** a caller supplies an `agent.md` path to a supported judge backend factory
- **THEN** the framework SHALL create an executable AWorld judge agent from the markdown metadata and body

#### Scenario: Existing system-prompt judge remains compatible
- **WHEN** a caller uses the existing `AgentJudgeBackend(system_prompt=...)` form
- **THEN** behavior SHALL remain compatible

#### Scenario: Markdown agent execution is trusted
- **WHEN** markdown agent loading is used
- **THEN** the framework SHALL treat the markdown definition as trusted local evaluator configuration and SHALL NOT execute arbitrary shell commands from the file during loading

### Requirement: Explicit judge payload normalization

Suite-backed judge validation SHALL support explicit payload normalization before typed schema validation.

#### Scenario: Suite declares a normalizer
- **WHEN** a suite or judge schema declares a payload normalizer
- **THEN** the framework SHALL apply that normalizer before typed model validation, metric extraction, and report assembly

#### Scenario: Dimensions-style trajectory judge output is normalized
- **WHEN** a trajectory judge output contains `weighted_score` and nested `dimensions.<metric>.score` fields and the suite opts into the built-in trajectory normalizer
- **THEN** the framework SHALL normalize the payload into flat `score` and metric fields before validation

#### Scenario: No hidden global normalization
- **WHEN** a judge output contains nested dimensions but no normalizer is configured
- **THEN** the framework SHALL preserve current validation behavior and SHALL NOT silently flatten the payload
