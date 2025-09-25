# AWorld Evaluations Module

The `aworld.evaluations` module provides a comprehensive framework for evaluating the performance of AI agents, language
models, and tasks within the AWorld ecosystem. It offers flexible evaluation criteria, diverse scoring mechanisms, and a
robust runtime system to conduct structured assessments.

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

## Evaluation Targets

### AworldAgentEvalTarget

`AworldAgentEvalTarget` enables evaluating AWorld agents by running them on evaluation datasets and capturing their
responses.

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
evaluation case.

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