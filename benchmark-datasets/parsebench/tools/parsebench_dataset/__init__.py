"""Client-owned pinned ParseBench Dataset preparation and scoring support."""

from .artifacts import (
    DEFAULT_PROVIDER,
    DEFAULT_VLM_MODEL_PROFILE,
    LAYOUT_MODEL_MANIFEST_SHA256,
    LAYOUT_MODEL_NAME,
    FileXAdapterError,
    FileXExecutionOptions,
    FileXRunResult,
    execute_filex_parsebench,
    load_parsebench_task_spec,
    validate_parsebench_artifacts,
)
from .contracts import (
    DATASET_REVISION,
    PARSEBENCH_TASK_FILENAME,
    PARSEBENCH_TASK_RUNTIME_PATH,
    PARSEBENCH_TASK_SCHEMA_VERSION,
    PINNED_PARSEBENCH_CONTRACT,
    SCORER_REVISION,
    ParseBenchContract,
    ParseBenchDatasetError,
    ParseBenchDimension,
    SmokeSelection,
)

__all__ = (
    "DATASET_REVISION",
    "DEFAULT_PROVIDER",
    "DEFAULT_VLM_MODEL_PROFILE",
    "LAYOUT_MODEL_MANIFEST_SHA256",
    "LAYOUT_MODEL_NAME",
    "PARSEBENCH_TASK_FILENAME",
    "PARSEBENCH_TASK_RUNTIME_PATH",
    "PARSEBENCH_TASK_SCHEMA_VERSION",
    "PINNED_PARSEBENCH_CONTRACT",
    "SCORER_REVISION",
    "FileXAdapterError",
    "FileXExecutionOptions",
    "FileXRunResult",
    "ParseBenchContract",
    "ParseBenchDatasetError",
    "ParseBenchDimension",
    "SmokeSelection",
    "execute_filex_parsebench",
    "load_parsebench_task_spec",
    "validate_parsebench_artifacts",
)
