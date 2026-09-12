"""Typed, version-pinned contracts shared by ParseBench authoring workflows."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
import re
from typing import TypeAlias


DATASET_REVISION = "2805a1d940f95a203e0ae4b88be9934f7765b3fc"
SCORER_REVISION = "34b73455032797754f6ed62e14c27a8b5423d11e"

DATASET_PACKAGE_SCHEMA_VERSION = "yolo-dataset-package/v2"
MATERIAL_MANIFEST_SCHEMA_VERSION = "yolo-dataset-material-manifest/v1"
TASK_ARCHIVE_SCHEMA_VERSION = "lingguang-task-archive/v1"
GROUND_TRUTH_SCHEMA_VERSION = "aworld-parsebench-ground-truth/v1"
PROVENANCE_SCHEMA_VERSION = "aworld-parsebench-provenance/v1"
CONVERTER_VERSION = "aworld-parsebench-dataset/v1"

EXPECTED_SOURCE_FIELDS = frozenset(
    {
        "pdf",
        "category",
        "id",
        "type",
        "rule",
        "page",
        "expected_markdown",
        "tags",
    }
)

JsonScalar: TypeAlias = str | int | float | bool | None
JsonValue: TypeAlias = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]

_REVISION_PATTERN = re.compile(r"^[0-9a-f]{40}$")


class ParseBenchDimension(str, Enum):
    """The five official, equal-weight ParseBench score groups."""

    TABLE = "table"
    CHART = "chart"
    TEXT_CONTENT = "text_content"
    TEXT_FORMATTING = "text_formatting"
    LAYOUT = "layout"


class ParseBenchDatasetError(ValueError):
    """Stable authoring error that never retains a source row or ground truth."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _validate_count_pairs(
    pairs: tuple[tuple[ParseBenchDimension, int], ...],
    *,
    name: str,
) -> None:
    dimensions: set[ParseBenchDimension] = set()
    for dimension, count in pairs:
        if not isinstance(dimension, ParseBenchDimension):
            raise TypeError(f"{name} contains an invalid dimension")
        if dimension in dimensions:
            raise ValueError(f"{name} contains a duplicate dimension")
        if isinstance(count, bool) or not isinstance(count, int) or count <= 0:
            raise ValueError(f"{name} contains an invalid count")
        dimensions.add(dimension)
    if dimensions != set(ParseBenchDimension):
        raise ValueError(f"{name} must cover all ParseBench dimensions")


@dataclass(frozen=True, slots=True)
class ParseBenchContract:
    """Pinned source, scorer, schema, and cardinality facts for one release."""

    dataset_revision: str
    scorer_revision: str
    source_files: tuple[tuple[ParseBenchDimension, str], ...]
    rule_counts: tuple[tuple[ParseBenchDimension, int], ...]
    dimension_execution_counts: tuple[tuple[ParseBenchDimension, int], ...]
    unique_execution_count: int

    def __post_init__(self) -> None:
        if _REVISION_PATTERN.fullmatch(self.dataset_revision) is None:
            raise ValueError("dataset_revision must be a lowercase Git commit")
        if _REVISION_PATTERN.fullmatch(self.scorer_revision) is None:
            raise ValueError("scorer_revision must be a lowercase Git commit")
        dimensions: set[ParseBenchDimension] = set()
        file_names: set[str] = set()
        for dimension, file_name in self.source_files:
            if not isinstance(dimension, ParseBenchDimension):
                raise TypeError("source_files contains an invalid dimension")
            if dimension in dimensions or file_name in file_names:
                raise ValueError("source_files contains a duplicate")
            if (
                not file_name
                or Path(file_name).name != file_name
                or not file_name.endswith(".jsonl")
            ):
                raise ValueError("source_files contains an invalid JSONL path")
            dimensions.add(dimension)
            file_names.add(file_name)
        if dimensions != set(ParseBenchDimension):
            raise ValueError("source_files must cover all ParseBench dimensions")
        _validate_count_pairs(self.rule_counts, name="rule_counts")
        _validate_count_pairs(
            self.dimension_execution_counts,
            name="dimension_execution_counts",
        )
        if (
            isinstance(self.unique_execution_count, bool)
            or not isinstance(self.unique_execution_count, int)
            or self.unique_execution_count <= 0
        ):
            raise ValueError("unique_execution_count must be positive")

    @property
    def total_rule_count(self) -> int:
        return sum(count for _, count in self.rule_counts)

    def source_file_for(self, dimension: ParseBenchDimension) -> str:
        return dict(self.source_files)[dimension]

    def expected_rules_for(self, dimension: ParseBenchDimension) -> int:
        return dict(self.rule_counts)[dimension]

    def expected_executions_for(self, dimension: ParseBenchDimension) -> int:
        return dict(self.dimension_execution_counts)[dimension]


PINNED_PARSEBENCH_CONTRACT = ParseBenchContract(
    dataset_revision=DATASET_REVISION,
    scorer_revision=SCORER_REVISION,
    source_files=(
        (ParseBenchDimension.CHART, "chart.jsonl"),
        (ParseBenchDimension.LAYOUT, "layout.jsonl"),
        (ParseBenchDimension.TABLE, "table.jsonl"),
        (ParseBenchDimension.TEXT_CONTENT, "text_content.jsonl"),
        (ParseBenchDimension.TEXT_FORMATTING, "text_formatting.jsonl"),
    ),
    rule_counts=(
        (ParseBenchDimension.CHART, 4_864),
        (ParseBenchDimension.LAYOUT, 16_325),
        (ParseBenchDimension.TABLE, 503),
        (ParseBenchDimension.TEXT_CONTENT, 141_322),
        (ParseBenchDimension.TEXT_FORMATTING, 5_997),
    ),
    dimension_execution_counts=(
        (ParseBenchDimension.CHART, 568),
        (ParseBenchDimension.LAYOUT, 500),
        (ParseBenchDimension.TABLE, 503),
        (ParseBenchDimension.TEXT_CONTENT, 506),
        (ParseBenchDimension.TEXT_FORMATTING, 476),
    ),
    unique_execution_count=2_078,
)


@dataclass(frozen=True, slots=True)
class FileEvidence:
    relative_path: str
    size: int
    sha256: str
    row_count: int | None = None


@dataclass(frozen=True, slots=True)
class ParseBenchRule:
    source_path: str
    dimension: ParseBenchDimension
    rule_id: str
    rule_type: str
    rule_payload: dict[str, JsonValue]
    page: int | None
    expected_markdown: str | None
    tags: tuple[str, ...]
    source_jsonl: str
    source_line: int


@dataclass(frozen=True, slots=True)
class ParseBenchExecution:
    task_id: str
    source_path: str
    source_file: Path
    source_size: int
    source_sha256: str
    page: int | None
    dimensions: tuple[ParseBenchDimension, ...]
    rules: tuple[ParseBenchRule, ...]


@dataclass(frozen=True, slots=True)
class ParseBenchSourceDataset:
    source_root: Path
    dataset_revision: str
    scorer_revision: str
    source_files: tuple[FileEvidence, ...]
    rules: tuple[ParseBenchRule, ...]
    executions: tuple[ParseBenchExecution, ...]


@dataclass(frozen=True, slots=True)
class SmokeSelection:
    """Deterministically choose a bounded number of executions per dimension."""

    per_dimension: int = 3
    seed: str = "parsebench-smoke-v1"

    def __post_init__(self) -> None:
        if (
            isinstance(self.per_dimension, bool)
            or not isinstance(self.per_dimension, int)
            or not 1 <= self.per_dimension <= 100
        ):
            raise ValueError("per_dimension must be between 1 and 100")
        if (
            not isinstance(self.seed, str)
            or not self.seed
            or self.seed != self.seed.strip()
            or len(self.seed) > 128
            or any(
                ord(character) < 32 or ord(character) == 127 for character in self.seed
            )
        ):
            raise ValueError("seed must be a bounded printable string")


@dataclass(frozen=True, slots=True)
class PackageBuildResult:
    output: Path
    package_sha256: str
    task_count: int
    rule_count: int
    dimensions: tuple[ParseBenchDimension, ...]
    selection: SmokeSelection | None


__all__ = (
    "CONVERTER_VERSION",
    "DATASET_PACKAGE_SCHEMA_VERSION",
    "DATASET_REVISION",
    "EXPECTED_SOURCE_FIELDS",
    "FileEvidence",
    "GROUND_TRUTH_SCHEMA_VERSION",
    "JsonScalar",
    "JsonValue",
    "MATERIAL_MANIFEST_SCHEMA_VERSION",
    "PINNED_PARSEBENCH_CONTRACT",
    "PROVENANCE_SCHEMA_VERSION",
    "PackageBuildResult",
    "ParseBenchContract",
    "ParseBenchDatasetError",
    "ParseBenchDimension",
    "ParseBenchExecution",
    "ParseBenchRule",
    "ParseBenchSourceDataset",
    "SCORER_REVISION",
    "SmokeSelection",
    "TASK_ARCHIVE_SCHEMA_VERSION",
)
