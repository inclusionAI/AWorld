# AWorld Evaluations Module

The `aworld.evaluations` module is the framework-owned evaluation substrate for AWorld. It supports both legacy
`EvaluationConfig`-driven flows and newer suite-backed evaluator flows that can execute an agent or task first, then
score both final outcomes and trajectory/process quality from a normalized execution state.

## Table of Contents

- [Core Components](#core-components)
- [Scorers System](#scorers-system)
- [Evaluation Targets](#evaluation-targets)
- [Evaluation Runtime](#evaluation-runtime)
- [Usage Examples](#usage-examples)
- [Module Structure](#module-structure)

## Core Components

### EvalTarget

`EvalTarget` is an abstract base class that defines the interface for objects to be evaluated. It provides a `predict`
method that should be implemented by subclasses to execute the model or agent and return results.

```python
class EvalTarget(abc.ABC, Generic[EvalCaseDataType]):
    def __init__(self, eval_config: EvaluationConfig = None):
        self.eval_config = eval_config or EvaluationConfig()

    @abc.abstractmethod
    async def predict(self, index: int, input: EvalDataCase[EvalCaseDataType]) -> dict:
        """Execute the llm/agent and return results."""
        raise NotImplementedError
```

### Scorer

`Scorer` is an abstract base class for evaluation scorers. It provides methods to score results against predefined
criteria and summarize scores across multiple evaluation cases.

```python
class Scorer(abc.ABC, Generic[EvalCaseDataType]):
    def __init__(self, name: str = None, eval_config: EvaluationConfig = None):
        self.name = name or self.__class__.__name__
        self.eval_criterias = {}
        self.eval_config = eval_config or EvaluationConfig()

    @abc.abstractmethod
    async def score(self, index: int, input: EvalDataCase[EvalCaseDataType], output: dict) -> ScorerResult:
        """Score the execute result."""
        raise NotImplementedError
```

### Evaluator

`Evaluator` coordinates the evaluation process by running evaluation cases through the target and applying scorers to
the results. It supports parallel execution and repeated runs for statistical robustness.

### EvalCriteria

`EvalCriteria` defines the metrics and thresholds for evaluation, including scoring rubrics, value ranges, and pass/fail
conditions.

```python
@dataclass
class EvalCriteria:
    metric_name: str = field(default_factory=str)
    scorer_class: Optional[str] = field(default_factory=str)
    scorer_params: Optional[dict] = field(default_factory=dict)
    prompt: str = field(default_factory=str)
    max_value: float = field(default=float('inf'))
    min_value: float = field(default=-float('inf'))
    threshold: float = field(default=0.0)
```

### EvalDataset and EvalDataCase

`EvalDataset` represents a collection of evaluation cases, while `EvalDataCase` represents a single evaluation instance
with input data.

### EvalResult

`EvalResult` captures the outcomes of an evaluation run, including individual case results and summary statistics.

### Suite-Backed Evaluation Definitions

Suite-backed evaluation adds a definition layer on top of the existing runtime skeleton:

- `EvalSuiteDef`: suite identity, cases, judge schema, gate policy, trajectory scorers, toolset hints, execution spec
- `EvalCaseDef`: input plus optional expected output and per-case runtime hints
- `EvalHarnessDef`: reusable execution defaults for suite-backed flows
- `EvalExecutionSpec`: runtime execution mode and target/task configuration
- `EvalState`: normalized execution result containing final answer, completion view, trajectory, usage, timing, and errors

These live under `aworld/evaluations/**`, not in `aworld-cli`, so the same substrate can be reused by framework callers,
official CLI flows, and custom evaluation agents.

Ownership is explicit:

- suite and case definitions own evaluation intent: input, expected outcome, task-domain tool hints, tags, and judge/gate semantics
- harnesses and execution specs own runtime behavior: whether execution is static, agent-backed, task-backed, or program-backed, plus task/runner configuration
- `aworld-cli` only assembles workspace inputs into these framework objects; it does not redefine evaluator semantics

`EvalState` intentionally separates:

- `answer`: the final deliverable or normalized terminal answer
- `completion`: completion-oriented view used by outcome scorers that only care about the final assistant output
- `trajectory`: full execution history used by process, tool-use, and efficiency scorers

## Scorers

### Scorer Registry

The scorer registry provides a centralized mechanism for registering and retrieving scorers. It supports automatic
registration using decorators and mapping between metrics and their associated scorers.

```python
@scorer_register(MetricNames.ANSWER_ACCURACY)
class AnswerAccuracyLLMScorer(LLMAsJudgeScorer):
# Implementation details
```

### LLM as Judge

The `LLMAsJudgeScorer` class enables using language models as evaluators. It provides a framework for building prompts,
sending them to a judge model, and interpreting the results.

```python
class LLMAsJudgeScorer(Scorer, Generic[EvalCaseDataType]):
    def __init__(self, model_config: ModelConfig = None):
        super().__init__()
        self.model_config = model_config or ModelConfig(...)

    @abc.abstractmethod
    def build_judge_prompt(self, index: int, input: EvalDataCase[EvalCaseDataType], output: dict) -> str:
        """Builds a prompt for the judge model."""
        raise NotImplementedError
```

### Built-in Scorers

The module includes several pre-built scorers for common evaluation tasks:

- **AnswerAccuracyLLMScorer**: Evaluates the accuracy of answers against reference solutions
- **LabelDistribution**: Analyzes the distribution of labels in model outputs
- **SummarizeQuality**: Assesses the quality of generated summaries
- And more...

### Execution-State Helpers

`aworld.evaluations.scorers.state_extractors` provides reusable helpers for execution-backed scoring:

- `get_eval_state(output)`
- `get_completion(output)`
- `get_assistant_messages(output)`
- `get_messages_by_role(output, role)`
- `get_tool_calls(output)`
- `get_trajectory(output)`

Use these helpers instead of hand-parsing raw trajectory payloads in every scorer.

## Evaluation Targets

### AworldAgentEvalTarget

`AworldAgentEvalTarget` enables evaluating AWorld agents by running them on evaluation datasets and capturing their
responses. In execution-backed flows it returns both the final answer and a normalized `state` payload.

```python
class AworldAgentEvalTarget(EvalTarget[dict]):
    def __init__(self, agent: Optional[Agent] = None, agent_config: Optional[dict | str] = None,
                 query_column: str = 'query'):

    # Initialization logic

    async def predict(self, index: int, input: EvalDataCase[dict]) -> dict:
# Agent execution logic
```

### AworldTaskEvalTarget

`AworldTaskEvalTarget` provides a framework for evaluating task-based systems by building and running tasks for each
evaluation case. In execution-backed flows it normalizes `TaskResponse` output into `EvalState`.

## Execution-Backed Suite Evaluation

Execution-backed suite flows reuse the existing AWorld runtime instead of introducing a parallel evaluator stack:

- suite/case definitions specify what is being evaluated
- `EvalExecutionSpec` specifies how runtime execution happens
- `EvalTarget -> Evaluator -> EvaluateRunner` remains the core orchestration skeleton
- scorers read normalized execution state for outcome and trajectory evaluation

The current execution modes are:

- `static`: judge-only evaluation with no runtime execution
- `agent`: execute through `AworldAgentEvalTarget`
- `task`: execute through `AworldTaskEvalTarget`
- `program`: execute an importable callable through the evaluator adapter layer and normalize the result into `EvalState`

This gives AWorld a framework-native evaluator path that can assess final artifacts, structured outputs, and trajectory
quality through one substrate.

Suite-backed evaluation also supports:

- typed judge schemas: Pydantic-backed validation with JSON schema export and required-field compatibility
- composite gates: structured metric conditions with `pass`, `fail`, and `needs_approval` outcomes
- trajectory scorers: suite-declared process metrics that lower into normal evaluator criteria and reports

## Suite, Case, and Execution Mapping

The evaluator v2 path is intentionally close to AWorld's existing runner model:

- suite -> describes the evaluation contract and default gate/judge behavior
- case -> provides per-row input, optional references, and case-local execution hints
- execution spec -> describes how a case becomes a runnable AWorld execution
- eval target -> adapts the execution spec into an existing target implementation
- evaluator / runner -> executes cases and produces normalized outputs
- scorers -> read final answer, completion, and trajectory from `EvalState`

In practice this means outcome evaluation and trajectory evaluation share one execution pipeline. A suite can score only
the final artifact, only the trajectory, or both.

## Recorder

The runtime system includes recorders for handling evaluation runs, datasets, and results, with default implementations
that can be extended or replaced as needed:

- **EvalRunRecorder**: Manages evaluation runs and their metadata
- **EvalDatasetRecorder**: Handles dataset loading and storage
- **EvalResultRecorder**: Manages result persistence and retrieval

## Evaluation Runner

`EvalRunner` orchestrates the complete evaluation process, including loading datasets, creating evaluation runs,
executing evaluations, and saving results.

```python
from aworld.core.task import Runner

class EvaluateRunner(Runner):
    async def do_run(self) -> EvalResult:
        """Run the evaluation."""
        # Evaluation orchestration logic
```

`EvaluateRunner` remains the orchestration layer. Suite-backed evaluation compiles into it rather than replacing it.

## Framework vs CLI

`aworld.evaluations` owns evaluation semantics:

- suite definitions
- execution-backed compilation
- normalized execution state
- scoring helpers
- report and declared-suite schemas

`aworld-cli` owns product entrypoints:

- `aworld-cli evaluator`
- workspace suite discovery
- report file writing
- evaluator lifecycle hooks for peripheral CLI customization

`aworld-cli evaluator` is now plugin-backed, but the reusable evaluator substrate remains framework-owned.

The intended layering is:

- build evaluator capabilities in `aworld/evaluations/**`
- expose a convenient official entrypoint in `aworld-cli`
- allow other agents or products to reuse the framework substrate without depending on the CLI command shape

## Usage Examples

### Basic Evaluation

```python
from aworld.evaluations.base import EvaluationConfig, EvalDataset, EvalDataCase
from aworld.evaluations.recoder.eval_runner import EvaluateRunner

# Create evaluation config
config = EvaluationConfig(
    eval_target_full_class_name="aworld.evaluations.eval_targets.agent_eval.AworldAgentEvalTarget",
    eval_target_config={"agent_config": {"llm_provider": "openai", "llm_model_name": "gpt-3.5-turbo"}},
    eval_dataset_id_or_file_path="path/to/eval_dataset.jsonl",
    eval_criterias=[{"metric_name": "answer_accuracy"}]
)

# Run evaluation
runner = EvaluateRunner()
result = await runner.eval_run(config)
```

### Creating a Custom Scorer

```python
from aworld.evaluations.scorers.scorer_registry import scorer_register
from aworld.evaluations.base import MetricResult, ScorerResult
from aworld.evaluations.scorers.metrics import MetricNames


@scorer_register(MetricNames.CUSTOM_METRIC)
class MyCustomScorer(Scorer):
    async def score(self, index: int, input: EvalDataCase, output: dict) -> ScorerResult:
        # Custom scoring logic
        score_value = ...  # Calculate score
        return ScorerResult(
            scorer_name=self.name,
            metric_results={MetricNames.CUSTOM_METRIC: {"value": score_value}}
        )
```

## Module Structure

```
evaluations/
├── __init__.py
├── base.py                # Core interfaces and data structures
├── eval_targets/          # Evaluation target implementations
│   ├── __init__.py
│   └── agent_eval.py      # Agent evaluation targets
├── evel_runtime/          # Evaluation runtime components
│   ├── __init__.py
│   ├── eval_dataset_manager.py  # Dataset management
│   ├── eval_result_manager.py   # Result management
│   ├── eval_run_manager.py      # Run management
│   └── eval_runner.py           # Evaluation orchestration
└── scorers/               # Scoring components
    ├── __init__.py
    ├── answer_accuracy.py       # Answer accuracy scoring
    ├── label_distribution.py    # Label distribution analysis
    ├── llm_as_judge.py          # LLM-based evaluation
    ├── metrics.py               # Metric definitions
    ├── scorer_registry.py       # Scorer registration system
    └── summarize_quality.py     # Summary quality assessment
```

## Key Features

- **Flexible Evaluation Framework**: Supports various evaluation targets, metrics, and scoring methods
- **Parallel Execution**: Optimizes evaluation speed through configurable parallelism
- **LLM-as-Judge Capabilities**: Leverages language models for nuanced evaluation tasks
- **Extensible Architecture**: Easy to add new scorers, targets, and evaluation methods
- **Statistical Analysis**: Provides summary statistics and pass@k metrics for robust evaluation

## Configuration

Evaluation behavior can be customized through the `EvaluationConfig` class, which allows specifying:

- Evaluation targets and their configurations
- Datasets and loading parameters
- Evaluation criteria and metrics
- Execution parameters like parallelism and repetition count
