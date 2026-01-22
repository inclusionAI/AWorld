"""
Batch job configuration models.
"""
from typing import Optional, Dict, Any
from dataclasses import dataclass, field


@dataclass
class InputConfig:
    """
    Input configuration for batch job.

    Example:
        >>> input_cfg = InputConfig(path="eval.csv", query_column="query")
    """
    path: str
    query_column: str = "query"
    encoding: str = "utf-8"
    delimiter: str = ","


@dataclass
class AgentConfig:
    """
    Agent configuration for batch job.

    Example:
        >>> agent_cfg = AgentConfig(name="PPTTeam", remote_backend="http://localhost:8000")
    """
    name: str
    remote_backend: Optional[str] = None


@dataclass
class OutputConfig:
    """
    Output configuration for batch job.

    Example:
        >>> output_cfg = OutputConfig(path="./result/output.csv")
    """
    path: str
    encoding: str = "utf-8"
    delimiter: str = ","


@dataclass
class ExecutionConfig:
    """
    Execution configuration for batch job.

    Example:
        >>> exec_cfg = ExecutionConfig(parallel=4)
    """
    parallel: int = 1
    max_retries: int = 0
    timeout_per_task: Optional[int] = None  # seconds


@dataclass
class BatchJobConfig:
    """
    Complete batch job configuration.

    Example:
        >>> job = BatchJobConfig(
        ...     input=InputConfig(path="eval.csv", query_column="query"),
        ...     agent=AgentConfig(name="PPTTeam", remote_backend="http://localhost:8000"),
        ...     output=OutputConfig(path="./result/output.csv"),
        ...     execution=ExecutionConfig(parallel=4)
        ... )
    """
    input: InputConfig
    agent: AgentConfig
    output: OutputConfig
    execution: ExecutionConfig = field(default_factory=ExecutionConfig)
