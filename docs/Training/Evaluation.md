AWorld’s evaluation system provides a **comprehensive and flexible evaluation framework** that supports diverse
evaluation scenarios and objectives—such as multi-agent systems, tool usage, and task completion quality. Built on a
modular design, it allows users to easily extend evaluation capabilities to meet specific needs, forming a critical
foundation for the continuous improvement of agents.

### Core Components

+ **Evaluation Target (**`**EvalTarget**`**)**  
  The central concept of the evaluation system, defining _what_ to evaluate and _how_ to evaluate it.
    - **Built-in Evaluation Targets**:
        * `AworldAgentEvalTarget`: Evaluates the performance of AWorld agents.
        * `AworldTaskEvalTarget`: Evaluates the completion quality of AWorld tasks.
    - **Custom Evaluation Targets**: Users can implement their own by subclassing `EvalTarget`. The core logic resides
      in the `predict` method, which executes the evaluation and returns results.
+ **Metrics**
    - **Functional Metrics**: Measure task accuracy and correctness.
    - **Performance Metrics**: Assess execution efficiency and resource consumption.
    - **Quality Metrics**: Evaluate output quality, fluency, diversity, etc.
+ **Evaluation Dataset (**`**EvalDataset**`**)**  
  Stores evaluation data in various formats (e.g., JSON, CSV). Each entry includes input data and expected output,
  enabling comparison against actual agent responses.
+ **EvaluateRunner**  
  Orchestrates the entire evaluation pipeline: loading datasets, executing evaluations, collecting results, and
  generating reports.

### Evaluation Workflow

![](../imgs/eval.png)

1. **Evaluation Configuration**  
   Before evaluation, configure:
    - Evaluation target type
    - Dataset path
    - Selected metrics
    - Evaluation strategy
    - Output directory
2. **Data Loading**  
   Load evaluation data (inputs + expected outputs) from the specified dataset in supported formats.
3. **Evaluation Execution**  
   For each data item:
    - Feed input to the evaluation target
    - Invoke the target’s `predict` method
    - Collect execution results
    - Compute metrics
    - Log the evaluation trace
4. **Result Analysis**  
   After completion, the framework provides:
    - **Detailed Results**: Per-case evaluation outcomes
    - **Execution Summary**: Overall success rate, average metrics, total runtime, etc.
    - **Evaluation Report**: Insights including failure analysis, performance breakdowns, and visualizations
    - **Result Persistence**: Saves all results to the configured output directory

#### Code Example

```python
from aworld.evaluations.base import Evaluator, EvalTarget, EvalDataCase
from aworld.core.task import Task
from aworld.runner import Runners


class MyEvalTarget(EvalTarget):
    async def predict(self, index: int, input: EvalDataCase[dict]) -> dict:
        # Extract input and expected output
        task_input = input.case_data["input"]
        expected_output = input.case_data["expected"]

        # Execute the task
        result = await Runners.run_task(
            task=Task(input=task_input, agent=my_agent)
        )

        # Return evaluation metrics
        return {
            "success": result.answer == expected_output,
            "accuracy": calculate_accuracy(result.answer, expected_output),
            "time_cost": result.time_cost
        }


# Create evaluator
evaluator = Evaluator(
    eval_target=MyEvalTarget(),
    dataset=my_dataset
)

# Run evaluation
results = await evaluator.evaluate()
```

### Best Practices

Evaluation framework's modular, extensible design ensures that AWorld’s evaluation system remains both powerful
out-of-the-box and adaptable to advanced research or production requirements.

The framework supports four common evaluation patterns:

1. **Single-Item Evaluation**: Assess individual cases for precise performance testing.
2. **Batch Evaluation**: Evaluate an entire dataset for holistic performance analysis.
3. **Comparative Evaluation**: Compare multiple models or agent configurations.
4. **Progressive Evaluation**: Gradually increase task complexity to probe capability boundaries.

#### Custom Evaluation Metrics

Users can define custom metrics by implementing the `Scorer` interface:

```python
from aworld.evaluations.base import Scorer


class CustomScore(Scorer):
    async def score(self, index: int, input: EvalDataCase[EvalCaseDataType], output: dict) -> ScorerResult:
        # Implement custom scoring logic
        return custom_score
```

#### Custom Evaluation Strategies

Custom evaluation logic can be implemented by inheriting from `EvalTarget`:

```python

class CustomEvalTarget(EvalTarget):
    async def predict(self, index: int, input: EvalDataCase[dict]) -> dict:
        # Implement domain-specific evaluation strategy
        pass
```
