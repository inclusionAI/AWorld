"""
Batch job runner for the batch inner plugin.

Encapsulates the core execution logic for batch jobs.
"""
import asyncio
from typing import Optional

from .loader import load_batch_config
from .executor import BatchExecutor


async def run_batch_job(config_path: str, remote_backend: Optional[str] = None) -> None:
    """
    Run a batch job with the given configuration.

    This function loads the batch configuration, optionally overrides
    the remote backend, and executes the batch job.

    Args:
        config_path: Path to batch job YAML configuration file.
        remote_backend: Optional remote backend URL to override config.

    Raises:
        FileNotFoundError: If config file doesn't exist.
        ValueError: If config is invalid.
        Exception: If batch execution fails.

    Example:
        >>> await run_batch_job("batch.yaml")
        >>> await run_batch_job("batch.yaml", remote_backend="http://localhost:8000")
    """
    config = load_batch_config(config_path)
    if remote_backend:
        config.agent.remote_backend = remote_backend

    executor = BatchExecutor()
    await executor.run(config)
