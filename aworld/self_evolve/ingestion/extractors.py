from __future__ import annotations

import ast
import csv
import hashlib
import io
import json
import os
import re
import stat
from pathlib import Path
from typing import Any, Iterable, Mapping

import yaml

from .types import (
    DatasetExtractor,
    ExtractedDocument,
    ExtractedRecord,
    IngestionContractError,
    IngestionLimits,
    IngestorTrustLevel,
    SourceAsset,
    fingerprint_json,
)


JSON_MEDIA_TYPE = "application/json"
JSONL_MEDIA_TYPE = "application/x-ndjson"
CSV_MEDIA_TYPE = "text/csv"
TSV_MEDIA_TYPE = "text/tab-separated-values"
YAML_MEDIA_TYPE = "application/yaml"
MARKDOWN_MEDIA_TYPE = "text/markdown"
LOG_MEDIA_TYPE = "text/x-log"
AWORLD_TRAJECTORY_LOG_MEDIA_TYPE = (
    "application/x-aworld-trajectory-log"
)
TEXT_MEDIA_TYPE = "text/plain"
BINARY_MEDIA_TYPE = "application/octet-stream"


def detect_media_type(path: Path, sample: bytes) -> str:
    """Detect only formats handled by the built-in, non-executable extractors."""

    suffix = path.suffix.lower()
    if b"\x00" in sample:
        return BINARY_MEDIA_TYPE
    try:
        text = sample.decode("utf-8")
    except UnicodeDecodeError:
        return BINARY_MEDIA_TYPE
    stripped = text.lstrip("\ufeff \t\r\n")

    if suffix in {".yaml", ".yml"}:
        return YAML_MEDIA_TYPE
    if suffix == ".csv":
        return CSV_MEDIA_TYPE
    if suffix == ".tsv":
        return TSV_MEDIA_TYPE
    if suffix in {".md", ".markdown"}:
        return MARKDOWN_MEDIA_TYPE
    if suffix in {".log"}:
        return (
            AWORLD_TRAJECTORY_LOG_MEDIA_TYPE
            if _looks_like_aworld_trajectory_log(stripped)
            else LOG_MEDIA_TYPE
        )
    if suffix in {".jsonl", ".ndjson"}:
        return JSONL_MEDIA_TYPE
    if suffix == ".json":
        return JSON_MEDIA_TYPE

    if stripped.startswith(("{", "[")):
        try:
            json.loads(stripped)
        except json.JSONDecodeError:
            if _looks_like_jsonl(stripped):
                return JSONL_MEDIA_TYPE
        else:
            return JSON_MEDIA_TYPE
    if _looks_like_jsonl(stripped):
        return JSONL_MEDIA_TYPE
    if suffix in {".txt", ".text"}:
        return TEXT_MEDIA_TYPE
    if suffix in {".yaml", ".yml"}:
        return YAML_MEDIA_TYPE
    return TEXT_MEDIA_TYPE


class JsonExtractor:
    name = "json"
    version = "1"
    trust_level = IngestorTrustLevel.FRAMEWORK_BUILTIN

    def supports(self, asset: SourceAsset) -> bool:
        return asset.media_type == JSON_MEDIA_TYPE

    def extract(
        self,
        asset_path: Path,
        *,
        asset: SourceAsset,
        limits: IngestionLimits,
    ) -> ExtractedDocument:
        value = _read_json(asset_path, asset=asset, limits=limits)
        if isinstance(value, list):
            if len(value) > limits.max_cases:
                raise IngestionContractError(
                    "source_limit_exceeded",
                    "JSON record count exceeds max_cases",
                )
            records = tuple(
                ExtractedRecord(locator=f"$[{index}]", value=item)
                for index, item in enumerate(value)
            )
        else:
            records = (ExtractedRecord(locator="$", value=value),)
        return _document(self, asset, records, _profile(value))


class JsonLinesExtractor:
    name = "jsonl"
    version = "1"
    trust_level = IngestorTrustLevel.FRAMEWORK_BUILTIN

    def supports(self, asset: SourceAsset) -> bool:
        return asset.media_type == JSONL_MEDIA_TYPE

    def extract(
        self,
        asset_path: Path,
        *,
        asset: SourceAsset,
        limits: IngestionLimits,
    ) -> ExtractedDocument:
        text = _read_text(asset_path, asset=asset, limits=limits)
        records: list[ExtractedRecord] = []
        invalid_lines: list[int] = []
        for line_number, line in enumerate(text.splitlines(), start=1):
            if not line.strip():
                continue
            if len(records) + len(invalid_lines) >= limits.max_cases:
                raise IngestionContractError(
                    "source_limit_exceeded",
                    "JSONL record count exceeds max_cases",
                )
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                invalid_lines.append(line_number)
                continue
            records.append(
                ExtractedRecord(locator=f"line:{line_number}", value=value)
            )
        profile = _records_profile(record.value for record in records)
        profile["invalid_record_count"] = len(invalid_lines)
        profile["invalid_record_locators"] = [
            f"line:{line_number}" for line_number in invalid_lines
        ]
        return _document(self, asset, tuple(records), profile)


class DelimitedTextExtractor:
    name = "delimited"
    version = "1"
    trust_level = IngestorTrustLevel.FRAMEWORK_BUILTIN

    def supports(self, asset: SourceAsset) -> bool:
        return asset.media_type in {CSV_MEDIA_TYPE, TSV_MEDIA_TYPE}

    def extract(
        self,
        asset_path: Path,
        *,
        asset: SourceAsset,
        limits: IngestionLimits,
    ) -> ExtractedDocument:
        text = _read_text(asset_path, asset=asset, limits=limits)
        delimiter = "\t" if asset.media_type == TSV_MEDIA_TYPE else ","
        reader = csv.DictReader(io.StringIO(text), delimiter=delimiter)
        records_list: list[ExtractedRecord] = []
        for index, row in enumerate(reader, start=2):
            if len(records_list) >= limits.max_cases:
                raise IngestionContractError(
                    "source_limit_exceeded",
                    "delimited record count exceeds max_cases",
                )
            records_list.append(
                ExtractedRecord(locator=f"row:{index}", value=dict(row))
            )
        records = tuple(records_list)
        profile = _records_profile(record.value for record in records)
        profile["columns"] = sorted(reader.fieldnames or ())
        return _document(self, asset, records, profile)


class YamlExtractor:
    name = "yaml"
    version = "1"
    trust_level = IngestorTrustLevel.FRAMEWORK_BUILTIN

    def supports(self, asset: SourceAsset) -> bool:
        return asset.media_type == YAML_MEDIA_TYPE

    def extract(
        self,
        asset_path: Path,
        *,
        asset: SourceAsset,
        limits: IngestionLimits,
    ) -> ExtractedDocument:
        value = yaml.safe_load(
            _read_text(asset_path, asset=asset, limits=limits)
        )
        try:
            if isinstance(value, list):
                if len(value) > limits.max_cases:
                    raise IngestionContractError(
                        "source_limit_exceeded",
                        "YAML record count exceeds max_cases",
                    )
                records = tuple(
                    ExtractedRecord(locator=f"$[{index}]", value=item)
                    for index, item in enumerate(value)
                )
            else:
                records = (ExtractedRecord(locator="$", value=value),)
            return _document(self, asset, records, _profile(value))
        except (TypeError, ValueError) as exc:
            raise IngestionContractError(
                "record_parse_failed",
                "YAML contains a value outside the JSON-compatible contract",
            ) from exc


class PlainTextExtractor:
    name = "text"
    version = "1"
    trust_level = IngestorTrustLevel.FRAMEWORK_BUILTIN

    def supports(self, asset: SourceAsset) -> bool:
        return asset.media_type in {
            TEXT_MEDIA_TYPE,
            MARKDOWN_MEDIA_TYPE,
            LOG_MEDIA_TYPE,
        }

    def extract(
        self,
        asset_path: Path,
        *,
        asset: SourceAsset,
        limits: IngestionLimits,
    ) -> ExtractedDocument:
        text = _read_text(asset_path, asset=asset, limits=limits)
        lines = text.splitlines()
        delimiter_candidates = sorted(
            {
                stripped
                for line in lines[:512]
                for stripped in (line.strip(),)
                if 1 <= len(stripped) <= 128
                and not any(character.isalnum() for character in stripped)
            }
        )[:16]
        profile = {
            "root_type": "string",
            "character_count": len(text),
            "line_count": len(lines),
            "non_empty_line_count": sum(
                1 for line in lines if line.strip()
            ),
            "value_shape": _scalar_shape(text),
            "literal_delimiter_candidates": delimiter_candidates,
            "line_shape_sample": [
                {
                    "line_number": index,
                    "length": len(line),
                    "blank": not bool(line.strip()),
                    "value_shape": _scalar_shape(line),
                }
                for index, line in enumerate(lines[:64], start=1)
            ],
        }
        return _document(
            self,
            asset,
            (ExtractedRecord(locator="$", value=text),),
            profile,
        )


class AWorldTrajectoryLogExtractor:
    name = "aworld_trajectory_log"
    version = "1"
    trust_level = IngestorTrustLevel.FRAMEWORK_BUILTIN

    def supports(self, asset: SourceAsset) -> bool:
        return asset.media_type == AWORLD_TRAJECTORY_LOG_MEDIA_TYPE

    def extract(
        self,
        asset_path: Path,
        *,
        asset: SourceAsset,
        limits: IngestionLimits,
    ) -> ExtractedDocument:
        text = _read_text(asset_path, asset=asset, limits=limits)
        records: list[ExtractedRecord] = []
        invalid_locators: list[str] = []
        for line_number, raw_line in enumerate(text.splitlines(), start=1):
            if not raw_line.strip():
                continue
            if len(records) + len(invalid_locators) >= limits.max_cases:
                raise IngestionContractError(
                    "source_limit_exceeded",
                    "trajectory log record count exceeds max_cases",
                )
            payload = _aworld_trajectory_log_record(raw_line)
            if payload is None:
                invalid_locators.append(f"line:{line_number}")
                continue
            raw_trajectory = payload.get("trajectory")
            try:
                trajectory = (
                    json.loads(raw_trajectory)
                    if isinstance(raw_trajectory, str)
                    else raw_trajectory
                )
            except json.JSONDecodeError:
                trajectory = None
            if not isinstance(trajectory, list):
                invalid_locators.append(f"line:{line_number}")
                continue
            records.append(
                ExtractedRecord(
                    locator=f"line:{line_number}",
                    value={
                        **{
                            str(key): value
                            for key, value in payload.items()
                            if key != "trajectory"
                        },
                        "trajectory": [
                            item
                            for item in trajectory
                            if isinstance(item, Mapping)
                        ],
                    },
                )
            )
        profile = _records_profile(record.value for record in records)
        profile["format"] = "aworld_trajectory_log"
        profile["invalid_record_count"] = len(invalid_locators)
        profile["invalid_record_locators"] = invalid_locators
        return _document(self, asset, tuple(records), profile)


_BUILTIN_EXTRACTORS: tuple[DatasetExtractor, ...] = (
    JsonExtractor(),
    JsonLinesExtractor(),
    DelimitedTextExtractor(),
    YamlExtractor(),
    AWorldTrajectoryLogExtractor(),
    PlainTextExtractor(),
)


def builtin_extractors() -> tuple[DatasetExtractor, ...]:
    return _BUILTIN_EXTRACTORS


def extractor_for(
    asset: SourceAsset,
    *,
    extractors: Iterable[DatasetExtractor] | None = None,
) -> DatasetExtractor | None:
    for extractor in extractors or _BUILTIN_EXTRACTORS:
        if extractor.supports(asset):
            return extractor
    return None


def extract_asset(
    asset_path: Path,
    *,
    asset: SourceAsset,
    limits: IngestionLimits,
    extractors: Iterable[DatasetExtractor] | None = None,
) -> ExtractedDocument:
    extractor = extractor_for(asset, extractors=extractors)
    if extractor is None:
        raise IngestionContractError(
            "unsupported_media_type",
            f"no registered extractor supports {asset.media_type}",
        )
    document = extractor.extract(asset_path, asset=asset, limits=limits)
    if document.asset_id != asset.asset_id:
        raise IngestionContractError(
            "fingerprint_mismatch",
            "extractor returned a document for another asset",
        )
    return document


def extractor_fingerprint(extractor: DatasetExtractor) -> str:
    return fingerprint_json(
        {
            "name": extractor.name,
            "version": extractor.version,
            "contract": "aworld.self_evolve.dataset_extractor.v1",
        }
    )


def _document(
    extractor: DatasetExtractor,
    asset: SourceAsset,
    records: tuple[ExtractedRecord, ...],
    profile: Mapping[str, Any],
) -> ExtractedDocument:
    return ExtractedDocument(
        asset_id=asset.asset_id,
        media_type=asset.media_type,
        records=records,
        structural_profile=dict(profile),
        extractor_name=extractor.name,
        extractor_version=extractor.version,
    )


def read_regular_file(
    path: Path,
    *,
    max_bytes: int,
    expected_fingerprint: str | None = None,
    source_root: Path | None = None,
    relative_path: str | None = None,
) -> bytes:
    """Read one regular file without following a final symlink.

    The descriptor is opened non-blocking, validated with ``fstat`` and read in
    bounded chunks. This prevents a lstat/read race from turning a source asset
    into a symlink, FIFO, device, or unbounded allocation.
    """

    try:
        descriptor = _open_regular_descriptor(
            path,
            source_root=source_root,
            relative_path=relative_path,
        )
    except OSError as exc:
        raise IngestionContractError(
            "source_changed_during_ingestion",
            "source asset could not be opened safely",
        ) from exc
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise IngestionContractError(
                "source_changed_during_ingestion",
                "source asset is no longer a regular file",
            )
        if before.st_size > max_bytes:
            raise IngestionContractError(
                "source_limit_exceeded",
                "asset exceeds max_file_bytes",
            )
        chunks: list[bytes] = []
        observed = 0
        digest = hashlib.sha256()
        while True:
            chunk = os.read(descriptor, min(1024 * 1024, max_bytes + 1 - observed))
            if not chunk:
                break
            observed += len(chunk)
            if observed > max_bytes:
                raise IngestionContractError(
                    "source_limit_exceeded",
                    "asset grew beyond max_file_bytes while being read",
                )
            digest.update(chunk)
            chunks.append(chunk)
        after = os.fstat(descriptor)
        if (
            before.st_dev != after.st_dev
            or before.st_ino != after.st_ino
            or before.st_size != after.st_size
            or before.st_mtime_ns != after.st_mtime_ns
            or observed != after.st_size
        ):
            raise IngestionContractError(
                "source_changed_during_ingestion",
                "source asset changed while being read",
            )
        actual_fingerprint = "sha256:" + digest.hexdigest()
        if (
            expected_fingerprint is not None
            and actual_fingerprint != expected_fingerprint
        ):
            raise IngestionContractError(
                "source_changed_during_ingestion",
                "source asset no longer matches its frozen fingerprint",
            )
        return b"".join(chunks)
    finally:
        os.close(descriptor)


def fingerprint_regular_file(
    path: Path,
    *,
    max_bytes: int,
    sample_bytes: int = 0,
    source_root: Path | None = None,
    relative_path: str | None = None,
) -> tuple[str, int, bytes]:
    try:
        descriptor = _open_regular_descriptor(
            path,
            source_root=source_root,
            relative_path=relative_path,
        )
    except OSError as exc:
        raise IngestionContractError(
            "source_changed_during_ingestion",
            "source asset could not be opened safely",
        ) from exc
    try:
        before = os.fstat(descriptor)
        if before.st_size > max_bytes:
            raise IngestionContractError(
                "source_limit_exceeded",
                "asset exceeds the bounded fingerprint limit",
            )
        digest = hashlib.sha256()
        sample = bytearray()
        observed = 0
        while True:
            chunk = os.read(
                descriptor,
                min(1024 * 1024, max_bytes + 1 - observed),
            )
            if not chunk:
                break
            observed += len(chunk)
            if observed > max_bytes:
                raise IngestionContractError(
                    "source_limit_exceeded",
                    "asset grew beyond the bounded fingerprint limit",
                )
            digest.update(chunk)
            if len(sample) < sample_bytes:
                sample.extend(chunk[: sample_bytes - len(sample)])
        after = os.fstat(descriptor)
        if (
            before.st_dev != after.st_dev
            or before.st_ino != after.st_ino
            or before.st_size != after.st_size
            or before.st_mtime_ns != after.st_mtime_ns
            or observed != after.st_size
        ):
            raise IngestionContractError(
                "source_changed_during_ingestion",
                "source asset changed while being fingerprinted",
            )
        return "sha256:" + digest.hexdigest(), observed, bytes(sample)
    finally:
        os.close(descriptor)


def _open_regular_descriptor(
    path: Path,
    *,
    source_root: Path | None,
    relative_path: str | None,
) -> int:
    read_flags = (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_NONBLOCK", 0)
    )
    if source_root is None or relative_path is None:
        descriptor = os.open(path, read_flags)
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            os.close(descriptor)
            raise OSError("source is not a regular file")
        return descriptor

    directory_flags = (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_DIRECTORY", 0)
    )
    # Open the caller-authorized root itself with O_NOFOLLOW. Resolving it here
    # would reintroduce a race where the root is replaced by a symlink between
    # inventory and extraction.
    root = source_root.absolute()
    components = Path(relative_path).parts
    if not components or any(part in {"", ".", ".."} for part in components):
        raise OSError("invalid relative source path")
    current = os.open(root, directory_flags)
    try:
        for component in components[:-1]:
            next_directory = os.open(
                component,
                directory_flags,
                dir_fd=current,
            )
            os.close(current)
            current = next_directory
        descriptor = os.open(
            components[-1],
            read_flags,
            dir_fd=current,
        )
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            os.close(descriptor)
            raise OSError("source is not a regular file")
        return descriptor
    finally:
        os.close(current)


def _read_text(
    path: Path,
    *,
    asset: SourceAsset,
    limits: IngestionLimits,
) -> str:
    raw = read_regular_file(
        path,
        max_bytes=limits.max_file_bytes,
        expected_fingerprint=asset.content_fingerprint,
        source_root=_source_root_for_asset(path, asset.relative_path),
        relative_path=asset.relative_path,
    )
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise IngestionContractError(
            "unsupported_media_type",
            "asset is not valid UTF-8 text",
        ) from exc


def _source_root_for_asset(path: Path, relative_path: str) -> Path:
    component_count = len(Path(relative_path).parts)
    if component_count < 1 or component_count > len(path.parents):
        raise IngestionContractError(
            "source_escape",
            "asset locator cannot be resolved beneath the source root",
        )
    return path.parents[component_count - 1]


def _read_json(
    path: Path,
    *,
    asset: SourceAsset,
    limits: IngestionLimits,
) -> Any:
    try:
        return json.loads(_read_text(path, asset=asset, limits=limits))
    except json.JSONDecodeError as exc:
        raise IngestionContractError(
            "record_parse_failed",
            "invalid JSON document",
        ) from exc


def _looks_like_jsonl(text: str) -> bool:
    lines = [line for line in text.splitlines() if line.strip()]
    if len(lines) < 2:
        return False
    try:
        values = [json.loads(line) for line in lines[:16]]
    except json.JSONDecodeError:
        return False
    return all(isinstance(value, Mapping) for value in values)


def _looks_like_aworld_trajectory_log(text: str) -> bool:
    for line in text.splitlines()[:16]:
        if not line.strip():
            continue
        if _aworld_trajectory_log_record(line) is not None:
            return True
    return False


def _aworld_trajectory_log_record(raw_line: str) -> Mapping[str, Any] | None:
    clean = re.sub(r"\x1b\[[0-9;]*m", "", raw_line).strip()
    start = clean.find("{")
    if start < 0:
        return None
    try:
        record = ast.literal_eval(clean[start:])
    except (SyntaxError, ValueError):
        return None
    if not isinstance(record, Mapping):
        return None
    if "task_id" not in record or "trajectory" not in record:
        return None
    return record


def _records_profile(values: Iterable[Any]) -> dict[str, Any]:
    materialized = list(values)
    profile = {
        "root_type": "records",
        "record_count": len(materialized),
        "field_names": sorted(
            {
                _public_field_name(key)
                for value in materialized
                if isinstance(value, Mapping)
                for key in value
            }
        )[:256],
        "record_types": sorted({_type_name(value) for value in materialized}),
    }
    null_counts: dict[str, int] = {}
    value_shapes: dict[str, set[str]] = {}
    for value in materialized[:256]:
        if not isinstance(value, Mapping):
            continue
        for key, item in value.items():
            name = _public_field_name(key)
            null_counts[name] = null_counts.get(name, 0) + int(item is None)
            value_shapes.setdefault(name, set()).add(_shape_name(item))
    denominator = max(1, len(materialized))
    profile["null_ratio"] = {
        key: round(count / denominator, 6)
        for key, count in sorted(null_counts.items())
    }
    profile["field_shapes"] = {
        key: sorted(values) for key, values in sorted(value_shapes.items())
    }
    return profile


def _profile(value: Any) -> dict[str, Any]:
    if isinstance(value, list):
        profile = _records_profile(value)
        profile["root_type"] = "array"
        profile["array_length"] = len(value)
        return profile
    if isinstance(value, Mapping):
        return {
            "root_type": "object",
            "field_names": sorted(_public_field_name(key) for key in value)[:256],
            "field_shapes": {
                _public_field_name(key): [_shape_name(item)]
                for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
            },
            "null_ratio": {
                _public_field_name(key): float(item is None)
                for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
            },
        }
    return {"root_type": _type_name(value), "value_shape": _shape_name(value)}


def _type_name(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, str):
        return "string"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, Mapping):
        return "object"
    if isinstance(value, list):
        return "array"
    return type(value).__name__


def _shape_name(value: Any) -> str:
    if isinstance(value, str):
        return _scalar_shape(value)
    if isinstance(value, Mapping):
        return f"object:{min(len(value), 100)}"
    if isinstance(value, list):
        return f"array:{min(len(value), 1000)}"
    return _type_name(value)


def _scalar_shape(value: str) -> str:
    length = len(value)
    if length == 0:
        bucket = "empty"
    elif length <= 16:
        bucket = "short"
    elif length <= 128:
        bucket = "medium"
    else:
        bucket = "long"
    character_classes = []
    if any(char.isalpha() for char in value):
        character_classes.append("alpha")
    if any(char.isdigit() for char in value):
        character_classes.append("digit")
    if any(not char.isalnum() and not char.isspace() for char in value):
        character_classes.append("symbol")
    return "string:" + bucket + ":" + ",".join(character_classes)


def _public_field_name(value: Any) -> str:
    name = "".join(
        char if char.isprintable() and char not in "\r\n\t" else " "
        for char in str(value)
    ).strip()
    if len(name) <= 128:
        return name
    return "field_" + fingerprint_json(name).removeprefix("sha256:")[:24]
