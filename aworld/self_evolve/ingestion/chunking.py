from __future__ import annotations

import bisect
import hashlib
import json
from dataclasses import dataclass
from itertools import chain
from pathlib import Path
from types import MappingProxyType
from typing import Any, Iterable, Iterator, Mapping, Sequence

from .extractors import (
    AWORLD_TRAJECTORY_LOG_MEDIA_TYPE,
    CSV_MEDIA_TYPE,
    JSONL_MEDIA_TYPE,
    JSON_MEDIA_TYPE,
    MARKDOWN_MEDIA_TYPE,
    TSV_MEDIA_TYPE,
    YAML_MEDIA_TYPE,
    extract_asset,
    read_regular_file,
)
from .types import (
    IngestionContractError,
    IngestionLimits,
    SourceAsset,
    SourceInventory,
    SourceKind,
    canonical_json_bytes,
    fingerprint_bytes,
    fingerprint_json,
    validate_fingerprint,
    validate_relative_path,
    validate_safe_id,
)


SEMANTIC_CHUNK_SCHEMA_VERSION = "aworld.self_evolve.semantic_chunk.v1"
STRUCTURED_SOURCE_UNIT_SCHEMA_VERSION = (
    "aworld.self_evolve.structured_source_unit.v1"
)
SOURCE_BUNDLE_SCHEMA_VERSION = "aworld.self_evolve.source_bundle.v1"

_STRUCTURED_MEDIA_TYPES = frozenset(
    {
        JSON_MEDIA_TYPE,
        JSONL_MEDIA_TYPE,
        CSV_MEDIA_TYPE,
        TSV_MEDIA_TYPE,
        YAML_MEDIA_TYPE,
        AWORLD_TRAJECTORY_LOG_MEDIA_TYPE,
    }
)


@dataclass(frozen=True)
class SemanticChunkingLimits:
    max_chunk_bytes: int = 32 * 1024
    overlap_bytes: int = 512
    max_semantic_prompt_bytes: int = 512 * 1024
    max_source_units: int = 100_000
    max_structure_depth: int = 64

    def __post_init__(self) -> None:
        for name in (
            "max_chunk_bytes",
            "max_semantic_prompt_bytes",
            "max_source_units",
            "max_structure_depth",
        ):
            _positive_int(getattr(self, name), field_name=name)
        if (
            isinstance(self.overlap_bytes, bool)
            or not isinstance(self.overlap_bytes, int)
            or self.overlap_bytes < 0
        ):
            raise IngestionContractError(
                "invalid_limit",
                "overlap_bytes must be a non-negative integer",
            )
        if self.overlap_bytes >= self.max_chunk_bytes:
            raise IngestionContractError(
                "invalid_limit",
                "overlap_bytes must be smaller than max_chunk_bytes",
            )


@dataclass(frozen=True)
class SemanticChunkV1:
    chunk_id: str
    source_unit_id: str
    asset_id: str
    relative_path: str
    media_type: str
    byte_start: int
    byte_end: int
    line_start: int
    line_end: int
    heading_path: tuple[str, ...]
    content_fingerprint: str
    raw_text: str
    schema_version: str = SEMANTIC_CHUNK_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != SEMANTIC_CHUNK_SCHEMA_VERSION:
            raise IngestionContractError(
                "schema_version_mismatch",
                "invalid semantic chunk schema",
            )
        validate_safe_id(self.chunk_id, field_name="chunk_id")
        validate_safe_id(
            self.source_unit_id,
            field_name="source_unit_id",
        )
        validate_fingerprint(self.asset_id, field_name="asset_id")
        object.__setattr__(
            self,
            "relative_path",
            validate_relative_path(self.relative_path),
        )
        if not isinstance(self.media_type, str) or not self.media_type:
            raise IngestionContractError(
                "schema_invalid",
                "media_type must be non-empty",
            )
        _span_bounds(
            self.byte_start,
            self.byte_end,
            self.line_start,
            self.line_end,
        )
        for heading in self.heading_path:
            if (
                not isinstance(heading, str)
                or not heading.strip()
                or len(heading) > 512
            ):
                raise IngestionContractError(
                    "schema_invalid",
                    "heading path contains an invalid component",
                )
        if not isinstance(self.raw_text, str) or not self.raw_text:
            raise IngestionContractError(
                "schema_invalid",
                "semantic chunk text must be non-empty",
            )
        validate_fingerprint(
            self.content_fingerprint,
            field_name="content_fingerprint",
        )
        if fingerprint_bytes(self.raw_text.encode("utf-8")) != (
            self.content_fingerprint
        ):
            raise IngestionContractError(
                "fingerprint_mismatch",
                "semantic chunk content fingerprint mismatch",
            )
        if len(self.raw_text.encode("utf-8")) != (
            self.byte_end - self.byte_start
        ):
            raise IngestionContractError(
                "source_span_invalid",
                "semantic chunk byte range does not match its content",
            )

    def public_projection(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "chunk_id": self.chunk_id,
            "source_unit_id": self.source_unit_id,
            "asset_id": self.asset_id,
            "relative_path": self.relative_path,
            "media_type": self.media_type,
            "byte_start": self.byte_start,
            "byte_end": self.byte_end,
            "line_start": self.line_start,
            "line_end": self.line_end,
            "heading_path": list(self.heading_path),
            "content_fingerprint": self.content_fingerprint,
        }

    def to_dict(self, *, include_private: bool = True) -> dict[str, Any]:
        result = self.public_projection()
        if include_private:
            result["raw_text"] = self.raw_text
        return result

    @classmethod
    def from_dict(
        cls,
        payload: Mapping[str, Any],
    ) -> "SemanticChunkV1":
        _require_schema(payload, SEMANTIC_CHUNK_SCHEMA_VERSION)
        if "raw_text" not in payload:
            raise IngestionContractError(
                "private_source_content_missing",
                "semantic chunk reload requires private text",
            )
        return cls(
            chunk_id=str(payload.get("chunk_id") or ""),
            source_unit_id=str(payload.get("source_unit_id") or ""),
            asset_id=str(payload.get("asset_id") or ""),
            relative_path=str(payload.get("relative_path") or ""),
            media_type=str(payload.get("media_type") or ""),
            byte_start=payload.get("byte_start"),  # type: ignore[arg-type]
            byte_end=payload.get("byte_end"),  # type: ignore[arg-type]
            line_start=payload.get("line_start"),  # type: ignore[arg-type]
            line_end=payload.get("line_end"),  # type: ignore[arg-type]
            heading_path=tuple(
                str(item) for item in payload.get("heading_path", ())
            ),
            content_fingerprint=str(
                payload.get("content_fingerprint") or ""
            ),
            raw_text=str(payload.get("raw_text") or ""),
        )


@dataclass(frozen=True)
class StructuredSourceUnitV1:
    source_unit_id: str
    asset_id: str
    relative_path: str
    media_type: str
    record_locator: str
    field_path: str | None
    unit_kind: str
    value_fingerprint: str
    value: Any
    provenance_status: str = "unresolved"
    provenance_reason: str = "structured_locator_without_physical_span"
    schema_version: str = STRUCTURED_SOURCE_UNIT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != STRUCTURED_SOURCE_UNIT_SCHEMA_VERSION:
            raise IngestionContractError(
                "schema_version_mismatch",
                "invalid structured source unit schema",
            )
        validate_safe_id(
            self.source_unit_id,
            field_name="source_unit_id",
        )
        validate_fingerprint(self.asset_id, field_name="asset_id")
        object.__setattr__(
            self,
            "relative_path",
            validate_relative_path(self.relative_path),
        )
        if self.unit_kind not in {"record", "field"}:
            raise IngestionContractError(
                "schema_invalid",
                "structured source unit kind must be record or field",
            )
        if self.provenance_status != "unresolved":
            raise IngestionContractError(
                "structured_provenance_invalid",
                "structured units without physical offsets must remain unresolved",
            )
        if (
            self.provenance_reason
            != "structured_locator_without_physical_span"
        ):
            raise IngestionContractError(
                "structured_provenance_invalid",
                "structured units require the canonical unresolved reason",
            )
        if (
            not isinstance(self.record_locator, str)
            or not self.record_locator
            or len(self.record_locator) > 512
        ):
            raise IngestionContractError(
                "schema_invalid",
                "record_locator must be bounded and non-empty",
            )
        if self.unit_kind == "field":
            if (
                not isinstance(self.field_path, str)
                or not self.field_path
                or len(self.field_path) > 1024
            ):
                raise IngestionContractError(
                    "schema_invalid",
                    "field source unit requires a bounded field_path",
                )
        elif self.field_path is not None:
            raise IngestionContractError(
                "schema_invalid",
                "record source unit cannot carry a field_path",
            )
        validate_fingerprint(
            self.value_fingerprint,
            field_name="value_fingerprint",
        )
        if (
            fingerprint_json(_thaw_json(self.value))
            != self.value_fingerprint
        ):
            raise IngestionContractError(
                "fingerprint_mismatch",
                "structured source value fingerprint mismatch",
            )
        canonical = json.loads(
            canonical_json_bytes(_thaw_json(self.value)).decode("utf-8")
        )
        object.__setattr__(self, "value", _freeze_json(canonical))

    def public_projection(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "source_unit_id": self.source_unit_id,
            "asset_id": self.asset_id,
            "relative_path": self.relative_path,
            "media_type": self.media_type,
            "record_locator": self.record_locator,
            "field_path": self.field_path,
            "unit_kind": self.unit_kind,
            "value_fingerprint": self.value_fingerprint,
            "provenance_status": self.provenance_status,
            "provenance_reason": self.provenance_reason,
        }

    def to_dict(self, *, include_private: bool = True) -> dict[str, Any]:
        result = self.public_projection()
        if include_private:
            result["value"] = _thaw_json(self.value)
        return result

    @classmethod
    def from_dict(
        cls,
        payload: Mapping[str, Any],
    ) -> "StructuredSourceUnitV1":
        _require_schema(payload, STRUCTURED_SOURCE_UNIT_SCHEMA_VERSION)
        if "value" not in payload:
            raise IngestionContractError(
                "private_source_content_missing",
                "structured unit reload requires its value",
            )
        return cls(
            source_unit_id=str(payload.get("source_unit_id") or ""),
            asset_id=str(payload.get("asset_id") or ""),
            relative_path=str(payload.get("relative_path") or ""),
            media_type=str(payload.get("media_type") or ""),
            record_locator=str(payload.get("record_locator") or ""),
            field_path=(
                str(payload["field_path"])
                if payload.get("field_path") is not None
                else None
            ),
            unit_kind=str(payload.get("unit_kind") or ""),
            value=payload.get("value"),
            value_fingerprint=str(
                payload.get("value_fingerprint") or ""
            ),
            provenance_status=str(
                payload.get("provenance_status") or ""
            ),
            provenance_reason=str(
                payload.get("provenance_reason") or ""
            ),
        )


@dataclass(frozen=True)
class SourceBundleV1:
    inventory_fingerprint: str
    chunks: tuple[SemanticChunkV1, ...]
    structured_units: tuple[StructuredSourceUnitV1, ...]
    schema_version: str = SOURCE_BUNDLE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != SOURCE_BUNDLE_SCHEMA_VERSION:
            raise IngestionContractError(
                "schema_version_mismatch",
                "invalid source bundle schema",
            )
        validate_fingerprint(
            self.inventory_fingerprint,
            field_name="inventory_fingerprint",
        )
        chunk_ids = [item.chunk_id for item in self.chunks]
        if len(chunk_ids) != len(set(chunk_ids)):
            raise IngestionContractError(
                "duplicate_identity",
                "source bundle contains duplicate chunk IDs",
            )
        source_unit_ids = [
            item.source_unit_id
            for item in (*self.chunks, *self.structured_units)
        ]
        if len(source_unit_ids) != len(set(source_unit_ids)):
            raise IngestionContractError(
                "duplicate_identity",
                "source bundle contains duplicate source unit IDs",
            )

    @property
    def source_unit_ids(self) -> tuple[str, ...]:
        return tuple(
            sorted(
                item.source_unit_id
                for item in (*self.chunks, *self.structured_units)
            )
        )

    @property
    def fingerprint(self) -> str:
        return fingerprint_json(self.public_projection())

    def public_projection(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "inventory_fingerprint": self.inventory_fingerprint,
            "chunks": [
                item.public_projection()
                for item in sorted(
                    self.chunks,
                    key=lambda value: (
                        value.relative_path,
                        value.byte_start,
                        value.byte_end,
                    ),
                )
            ],
            "structured_units": [
                item.public_projection()
                for item in sorted(
                    self.structured_units,
                    key=lambda value: (
                        value.relative_path,
                        value.record_locator,
                        value.unit_kind,
                        value.field_path or "",
                    ),
                )
            ],
            "source_unit_count": len(self.source_unit_ids),
        }

    def private_prompt_projection(
        self,
        *,
        max_bytes: int = 512 * 1024,
    ) -> dict[str, Any]:
        _positive_int(max_bytes, field_name="max_bytes")
        _preflight_private_projection_size(
            (
                item.to_dict(include_private=True)
                for item in chain(
                    self.chunks,
                    self.structured_units,
                )
            ),
            max_bytes=max_bytes,
        )
        result = {
            **self.public_projection(),
            "chunks": [
                item.to_dict(include_private=True)
                for item in sorted(
                    self.chunks,
                    key=lambda value: (
                        value.relative_path,
                        value.byte_start,
                        value.byte_end,
                    ),
                )
            ],
            "structured_units": [
                item.to_dict(include_private=True)
                for item in sorted(
                    self.structured_units,
                    key=lambda value: (
                        value.relative_path,
                        value.record_locator,
                        value.unit_kind,
                        value.field_path or "",
                    ),
                )
            ],
        }
        if len(canonical_json_bytes(result)) > max_bytes:
            raise IngestionContractError(
                "semantic_prompt_limit_exceeded",
                "private semantic source projection exceeds its byte limit",
            )
        return result

    def to_dict(self, *, include_private: bool = True) -> dict[str, Any]:
        result = {
            "schema_version": self.schema_version,
            "inventory_fingerprint": self.inventory_fingerprint,
            "chunks": [
                item.to_dict(include_private=include_private)
                for item in self.chunks
            ],
            "structured_units": [
                item.to_dict(include_private=include_private)
                for item in self.structured_units
            ],
            "fingerprint": self.fingerprint,
        }
        return result

    @classmethod
    def from_dict(
        cls,
        payload: Mapping[str, Any],
        *,
        limits: SemanticChunkingLimits | None = None,
    ) -> "SourceBundleV1":
        _require_schema(payload, SOURCE_BUNDLE_SCHEMA_VERSION)
        semantic_limits = limits or SemanticChunkingLimits()
        chunk_payloads = _sequence(payload.get("chunks", ()), "chunks")
        structured_payloads = _sequence(
            payload.get("structured_units", ()),
            "structured_units",
        )
        if len(chunk_payloads) + len(structured_payloads) > (
            semantic_limits.max_source_units
        ):
            raise IngestionContractError(
                "semantic_source_unit_limit_exceeded",
                "semantic source units exceed the configured limit",
            )
        _preflight_private_projection_size(
            chain(chunk_payloads, structured_payloads),
            max_bytes=semantic_limits.max_semantic_prompt_bytes,
        )
        bundle = cls(
            inventory_fingerprint=str(
                payload.get("inventory_fingerprint") or ""
            ),
            chunks=tuple(
                SemanticChunkV1.from_dict(_mapping(item))
                for item in chunk_payloads
            ),
            structured_units=tuple(
                StructuredSourceUnitV1.from_dict(_mapping(item))
                for item in structured_payloads
            ),
        )
        claimed = payload.get("fingerprint")
        if claimed is not None and claimed != bundle.fingerprint:
            raise IngestionContractError(
                "fingerprint_mismatch",
                "source bundle fingerprint mismatch",
            )
        bundle.private_prompt_projection(
            max_bytes=semantic_limits.max_semantic_prompt_bytes
        )
        return bundle


def build_source_bundle(
    source_path: str | Path,
    *,
    inventory: SourceInventory,
    ingestion_limits: IngestionLimits | None = None,
    chunking_limits: SemanticChunkingLimits | None = None,
) -> SourceBundleV1:
    """Build a deterministic, bounded semantic view of an inventory."""

    inventory.verify_fingerprint()
    limits = ingestion_limits or IngestionLimits()
    semantic_limits = chunking_limits or SemanticChunkingLimits()
    root = _source_root(source_path, inventory)
    rejected_ids = {
        item.asset_identity
        for item in inventory.rejected_assets
        if item.asset_identity is not None
    }
    if rejected_ids:
        raise IngestionContractError(
            "semantic_source_asset_rejected",
            "semantic source bundle cannot omit rejected inventory assets",
        )

    chunks: list[SemanticChunkV1] = []
    structured_units: list[StructuredSourceUnitV1] = []
    private_projection_bytes = 0
    for asset in sorted(
        inventory.assets,
        key=lambda item: item.relative_path,
    ):
        path = root.joinpath(*Path(asset.relative_path).parts)
        raw = read_regular_file(
            path,
            max_bytes=limits.max_file_bytes,
            expected_fingerprint=asset.content_fingerprint,
            source_root=root,
            relative_path=asset.relative_path,
        )
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise IngestionContractError(
                "unsupported_media_type",
                "semantic ingestion requires valid UTF-8 source text",
            ) from exc
        for chunk in _chunks_for_asset(
            asset,
            raw=raw,
            text=text,
            limits=semantic_limits,
        ):
            _ensure_source_unit_capacity(
                len(chunks) + len(structured_units),
                semantic_limits,
            )
            private_projection_bytes = (
                _consume_private_projection_capacity(
                    private_projection_bytes,
                    chunk.to_dict(include_private=True),
                    limits=semantic_limits,
                )
            )
            chunks.append(chunk)
        if asset.media_type in _STRUCTURED_MEDIA_TYPES:
            document = extract_asset(
                path,
                asset=asset,
                limits=limits,
            )
            for unit in _structured_units_for_asset(
                asset,
                document.records,
                max_structure_depth=(
                    semantic_limits.max_structure_depth
                ),
            ):
                _ensure_source_unit_capacity(
                    len(chunks) + len(structured_units),
                    semantic_limits,
                )
                private_projection_bytes = (
                    _consume_private_projection_capacity(
                        private_projection_bytes,
                        unit.to_dict(include_private=True),
                        limits=semantic_limits,
                    )
                )
                structured_units.append(unit)

    bundle = SourceBundleV1(
        inventory_fingerprint=inventory.source_root_fingerprint,
        chunks=tuple(chunks),
        structured_units=tuple(structured_units),
    )
    bundle.private_prompt_projection(
        max_bytes=semantic_limits.max_semantic_prompt_bytes
    )
    return bundle


def _source_root(
    source_path: str | Path,
    inventory: SourceInventory,
) -> Path:
    supplied = Path(source_path).expanduser().absolute()
    if supplied.is_symlink():
        raise IngestionContractError(
            "source_symlink_not_allowed",
            "source path cannot be a symlink",
        )
    if inventory.source_kind is SourceKind.FILE:
        if not supplied.is_file():
            raise IngestionContractError(
                "source_changed_during_ingestion",
                "source file is no longer available",
            )
        if (
            len(inventory.assets) != 1
            or inventory.assets[0].relative_path != supplied.name
        ):
            raise IngestionContractError(
                "source_inventory_mismatch",
                "file inventory does not match the supplied source",
            )
        return supplied.parent
    if not supplied.is_dir():
        raise IngestionContractError(
            "source_changed_during_ingestion",
            "source directory is no longer available",
        )
    return supplied


def _chunks_for_asset(
    asset: SourceAsset,
    *,
    raw: bytes,
    text: str,
    limits: SemanticChunkingLimits,
) -> Iterator[SemanticChunkV1]:
    if not raw:
        return
    line_starts = _line_starts(raw)
    blocks = _semantic_blocks(text, asset.media_type)
    for byte_start, byte_end, heading_path in blocks:
        for part_start, part_end in _bounded_utf8_ranges(
            raw,
            byte_start,
            byte_end,
            max_bytes=limits.max_chunk_bytes,
            overlap_bytes=limits.overlap_bytes,
        ):
            content = raw[part_start:part_end]
            if not content:
                continue
            raw_text = content.decode("utf-8")
            content_fingerprint = fingerprint_bytes(content)
            identity_payload = {
                "asset_id": asset.asset_id,
                "byte_start": part_start,
                "byte_end": part_end,
                "content_fingerprint": content_fingerprint,
            }
            digest = hashlib.sha256(
                canonical_json_bytes(identity_payload)
            ).hexdigest()
            yield SemanticChunkV1(
                chunk_id=f"chunk:{digest}",
                source_unit_id=f"unit:chunk:{digest}",
                asset_id=asset.asset_id,
                relative_path=asset.relative_path,
                media_type=asset.media_type,
                byte_start=part_start,
                byte_end=part_end,
                line_start=_line_for_offset(line_starts, part_start),
                line_end=_line_for_offset(
                    line_starts,
                    max(part_start, part_end - 1),
                ),
                heading_path=heading_path,
                content_fingerprint=content_fingerprint,
                raw_text=raw_text,
            )


def _semantic_blocks(
    text: str,
    media_type: str,
) -> list[tuple[int, int, tuple[str, ...]]]:
    if not text:
        return []
    lines = text.splitlines(keepends=True)
    if not lines:
        return [(0, len(text), ())]
    character_offsets = [0]
    byte_offsets = [0]
    for line in lines:
        character_offsets.append(character_offsets[-1] + len(line))
        byte_offsets.append(byte_offsets[-1] + len(line.encode("utf-8")))
    blocks: list[tuple[int, int, tuple[str, ...]]] = []
    heading_stack: list[str] = []
    start: int | None = None
    block_heading: tuple[str, ...] = ()
    in_fence = False

    def flush(end_line: int) -> None:
        nonlocal start
        if start is None:
            return
        char_start = character_offsets[start]
        char_end = character_offsets[end_line]
        if text[char_start:char_end]:
            blocks.append(
                (
                    byte_offsets[start],
                    byte_offsets[end_line],
                    block_heading,
                )
            )
        start = None

    for index, line in enumerate(lines):
        stripped = line.strip()
        if media_type == MARKDOWN_MEDIA_TYPE and stripped.startswith(
            ("```", "~~~")
        ):
            if in_fence:
                if start is None:
                    start = index
                flush(index + 1)
                in_fence = False
            else:
                flush(index)
                start = index
                block_heading = tuple(heading_stack)
                in_fence = True
            continue
        if in_fence:
            continue
        heading = _markdown_heading(stripped) if (
            media_type == MARKDOWN_MEDIA_TYPE
        ) else None
        if heading is not None:
            flush(index)
            level, title = heading
            heading_stack[:] = heading_stack[: level - 1]
            heading_stack.append(title)
            start = index
            block_heading = tuple(heading_stack)
            flush(index + 1)
            continue
        if not stripped:
            flush(index)
            continue
        delimiter = (
            media_type != MARKDOWN_MEDIA_TYPE
            and len(stripped) <= 128
            and not any(character.isalnum() for character in stripped)
        )
        if delimiter:
            flush(index)
            start = index
            block_heading = tuple(heading_stack)
            flush(index + 1)
            continue
        if start is None:
            start = index
            block_heading = tuple(heading_stack)
    flush(len(lines))
    return blocks


def _markdown_heading(stripped: str) -> tuple[int, str] | None:
    if not stripped.startswith("#"):
        return None
    level = len(stripped) - len(stripped.lstrip("#"))
    if level < 1 or level > 6:
        return None
    remainder = stripped[level:]
    if not remainder.startswith(" "):
        return None
    title = remainder.strip().strip("#").strip()
    if not title:
        return None
    return level, title[:512]


def _bounded_utf8_ranges(
    raw: bytes,
    start: int,
    end: int,
    *,
    max_bytes: int,
    overlap_bytes: int,
) -> Iterable[tuple[int, int]]:
    cursor = start
    while cursor < end:
        candidate_end = min(end, cursor + max_bytes)
        while (
            candidate_end > cursor
            and candidate_end < len(raw)
            and _is_utf8_continuation(raw[candidate_end])
        ):
            candidate_end -= 1
        if candidate_end <= cursor:
            raise IngestionContractError(
                "semantic_chunk_invalid",
                "unable to split UTF-8 content within the chunk bound",
            )
        yield cursor, candidate_end
        if candidate_end >= end:
            break
        next_cursor = max(start, candidate_end - overlap_bytes)
        while (
            next_cursor < candidate_end
            and _is_utf8_continuation(raw[next_cursor])
        ):
            next_cursor += 1
        if next_cursor <= cursor:
            next_cursor = candidate_end
        cursor = next_cursor


def _is_utf8_continuation(value: int) -> bool:
    return value & 0b1100_0000 == 0b1000_0000


def _line_starts(raw: bytes) -> tuple[int, ...]:
    starts = [0]
    for index, value in enumerate(raw):
        if value == 0x0A and index + 1 < len(raw):
            starts.append(index + 1)
    return tuple(starts)


def _line_for_offset(line_starts: Sequence[int], offset: int) -> int:
    return bisect.bisect_right(line_starts, offset)


def _structured_units_for_asset(
    asset: SourceAsset,
    records: Sequence[Any],
    *,
    max_structure_depth: int,
) -> Iterator[StructuredSourceUnitV1]:
    for record in records:
        yield _structured_unit(
            asset,
            record_locator=record.locator,
            field_path=None,
            unit_kind="record",
            value=record.value,
        )
        if isinstance(record.value, (Mapping, list, tuple)):
            for field_path, value in _leaf_fields(
                record.value,
                max_depth=max_structure_depth,
            ):
                yield _structured_unit(
                    asset,
                    record_locator=record.locator,
                    field_path=field_path,
                    unit_kind="field",
                    value=value,
                )


def _structured_unit(
    asset: SourceAsset,
    *,
    record_locator: str,
    field_path: str | None,
    unit_kind: str,
    value: Any,
) -> StructuredSourceUnitV1:
    value_fingerprint = fingerprint_json(value)
    digest = hashlib.sha256(
        canonical_json_bytes(
            {
                "asset_id": asset.asset_id,
                "record_locator": record_locator,
                "field_path": field_path,
                "unit_kind": unit_kind,
                "value_fingerprint": value_fingerprint,
            }
        )
    ).hexdigest()
    return StructuredSourceUnitV1(
        source_unit_id=f"unit:{unit_kind}:{digest}",
        asset_id=asset.asset_id,
        relative_path=asset.relative_path,
        media_type=asset.media_type,
        record_locator=record_locator,
        field_path=field_path,
        unit_kind=unit_kind,
        value_fingerprint=value_fingerprint,
        value=value,
    )


def _leaf_fields(
    value: Any,
    *,
    max_depth: int,
) -> Iterable[tuple[str, Any]]:
    stack: list[tuple[str, Any, int]] = [("$", value, 0)]
    while stack:
        path, current, depth = stack.pop()
        if isinstance(current, Mapping):
            if depth >= max_depth:
                raise IngestionContractError(
                    "semantic_structure_depth_exceeded",
                    "structured source exceeds the configured nesting depth",
                )
            children = []
            for key in sorted(current, key=lambda item: str(item)):
                escaped = (
                    str(key)
                    .replace("~", "~0")
                    .replace("/", "~1")
                )
                children.append(
                    (f"{path}/{escaped}", current[key], depth + 1)
                )
            stack.extend(reversed(children))
            continue
        if isinstance(current, (list, tuple)):
            if depth >= max_depth:
                raise IngestionContractError(
                    "semantic_structure_depth_exceeded",
                    "structured source exceeds the configured nesting depth",
                )
            stack.extend(
                (f"{path}/{index}", item, depth + 1)
                for index, item in reversed(tuple(enumerate(current)))
            )
            continue
        yield path, current


def _ensure_source_unit_capacity(
    current_count: int,
    limits: SemanticChunkingLimits,
) -> None:
    if current_count >= limits.max_source_units:
        raise IngestionContractError(
            "semantic_source_unit_limit_exceeded",
            "semantic source units exceed the configured limit",
        )


def _preflight_private_projection_size(
    items: Iterable[Any],
    *,
    max_bytes: int,
) -> None:
    _positive_int(max_bytes, field_name="max_bytes")
    consumed = 0
    for item in items:
        consumed += len(canonical_json_bytes(item))
        if consumed > max_bytes:
            raise IngestionContractError(
                "semantic_prompt_limit_exceeded",
                "private semantic source projection exceeds its byte limit",
            )


def _consume_private_projection_capacity(
    consumed: int,
    item: Any,
    *,
    limits: SemanticChunkingLimits,
) -> int:
    updated = consumed + len(canonical_json_bytes(item))
    if updated > limits.max_semantic_prompt_bytes:
        raise IngestionContractError(
            "semantic_prompt_limit_exceeded",
            "private semantic source projection exceeds its byte limit",
        )
    return updated


def _span_bounds(
    byte_start: int,
    byte_end: int,
    line_start: int,
    line_end: int,
) -> None:
    for name, value in (
        ("byte_start", byte_start),
        ("byte_end", byte_end),
        ("line_start", line_start),
        ("line_end", line_end),
    ):
        if isinstance(value, bool) or not isinstance(value, int):
            raise IngestionContractError(
                "source_span_invalid",
                f"{name} must be an integer",
            )
    if byte_start < 0 or byte_end <= byte_start:
        raise IngestionContractError(
            "source_span_invalid",
            "semantic chunk byte range must be non-empty and ordered",
        )
    if line_start < 1 or line_end < line_start:
        raise IngestionContractError(
            "source_span_invalid",
            "semantic chunk line range must be 1-based and ordered",
        )


def _require_schema(
    payload: Mapping[str, Any],
    expected: str,
) -> None:
    if payload.get("schema_version") != expected:
        raise IngestionContractError(
            "schema_version_mismatch",
            f"expected schema_version={expected}",
        )


def _mapping(value: Any) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise IngestionContractError(
            "schema_invalid",
            "expected an object",
        )
    return value


def _sequence(value: Any, field_name: str) -> Sequence[Any]:
    if (
        not isinstance(value, Sequence)
        or isinstance(value, (str, bytes, bytearray))
    ):
        raise IngestionContractError(
            "schema_invalid",
            f"{field_name} must be an array",
        )
    return value


def _positive_int(value: int, *, field_name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise IngestionContractError(
            "invalid_limit",
            f"{field_name} must be a positive integer",
        )


def _freeze_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType(
            {
                str(key): _freeze_json(item)
                for key, item in value.items()
            }
        )
    if isinstance(value, list):
        return tuple(_freeze_json(item) for item in value)
    return value


def _thaw_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): _thaw_json(item)
            for key, item in value.items()
        }
    if isinstance(value, tuple):
        return [_thaw_json(item) for item in value]
    return value
