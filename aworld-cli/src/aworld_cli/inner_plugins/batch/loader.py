"""
Batch job configuration loader from YAML files.
"""
import yaml
from pathlib import Path
from typing import Optional
from .config import BatchJobConfig, InputConfig, AgentConfig, OutputConfig, ExecutionConfig


def load_batch_config(config_path: str) -> BatchJobConfig:
    """
    Load batch job configuration from YAML file.

    Expected YAML format (minimal first version):
        input:
          path: eval.csv
          query_column: query
        agent:
          name: PPTTeam
          remote_backend: http://localhost:8000
        output:
          path: ./result/output.csv
        execution:
          parallel: 4

    Args:
        config_path: Path to YAML configuration file

    Returns:
        BatchJobConfig instance

    Raises:
        FileNotFoundError: If config file doesn't exist
        ValueError: If config is invalid

    Example:
        >>> config = load_batch_config("batch.yaml")
        >>> print(config.agent.name)
        PPTTeam
    """
    config_file = Path(config_path)
    if not config_file.exists():
        raise FileNotFoundError(f"📄 Config file not found: {config_path}")

    with open(config_file, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)

    if not data:
        raise ValueError("❌ Config file is empty")

    # Parse input config
    input_data = data.get("input", {})
    if "path" not in input_data:
        raise ValueError("❌ Missing required field: input.path")
    input_config = InputConfig(
        path=input_data["path"],
        query_column=input_data.get("query_column", "query"),
        encoding=input_data.get("encoding", "utf-8"),
        delimiter=input_data.get("delimiter", ",")
    )

    # Parse agent config
    agent_data = data.get("agent", {})
    if "name" not in agent_data:
        raise ValueError("❌ Missing required field: agent.name")
    agent_config = AgentConfig(
        name=agent_data["name"],
        remote_backend=agent_data.get("remote_backend")
    )

    # Parse output config
    output_data = data.get("output", {})
    if "path" not in output_data:
        raise ValueError("❌ Missing required field: output.path")
    output_config = OutputConfig(
        path=output_data["path"],
        encoding=output_data.get("encoding", "utf-8"),
        delimiter=output_data.get("delimiter", ",")
    )

    # Parse execution config
    exec_data = data.get("execution", {})
    execution_config = ExecutionConfig(
        parallel=exec_data.get("parallel", 1),
        max_retries=exec_data.get("max_retries", 0),
        timeout_per_task=exec_data.get("timeout_per_task")
    )

    return BatchJobConfig(
        input=input_config,
        agent=agent_config,
        output=output_config,
        execution=execution_config
    )
