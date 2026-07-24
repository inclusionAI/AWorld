from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path, PurePosixPath
from typing import Any, Mapping, Protocol, Sequence, runtime_checkable


SOURCE_INVENTORY_SCHEMA_VERSION = "aworld.self_evolve.source_inventory.v1"
EXTRACTED_DOCUMENT_SCHEMA_VERSION = "aworld.self_evolve.extracted_document.v1"
DATASET_INGESTION_REQUEST_SCHEMA_VERSION = (
    "aworld.self_evolve.dataset_ingestion_request.v1"
)
DATASET_MAPPING_SCHEMA_VERSION = "aworld.self_evolve.dataset_mapping.v1"
NORMALIZED_CASE_SCHEMA_VERSION = "aworld.self_evolve.normalized_case.v1"
REJECTED_RECORD_SCHEMA_VERSION = "aworld.self_evolve.rejected_record.v1"
INGESTION_QUALITY_REPORT_SCHEMA_VERSION = (
    "aworld.self_evolve.ingestion_quality_report.v1"
)
FROZEN_INGESTION_SNAPSHOT_SCHEMA_VERSION = (
    "aworld.self_evolve.frozen_ingestion_snapshot.v1"
)
SOURCE_MANIFEST_SCHEMA_VERSION = "aworld.self_evolve.source_manifest.v1"
DATASET_INGESTION_GATE_SCHEMA_VERSION = (
    "aworld.self_evolve.dataset_ingestion_gate.v1"
)

_FINGERPRINT_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
_SAFE_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$")
_SAFE_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")


class IngestionContractError(ValueError):
    """A stable, typed failure in the ingestion data contract."""

    def __init__(self, reason_code: str, message: str) -> None:
        if not re.fullmatch(r"[a-z][a-z0-9_]*", reason_code):
            raise ValueError("reason_code must be lower_snake_case")
        self.reason_code = reason_code
        super().__init__(message)


class SourceKind(str, Enum):
    FILE = "file"
    DIRECTORY = "directory"


class IngestorTrustLevel(str, Enum):
    FRAMEWORK_BUILTIN = "framework_builtin"
    WORKSPACE_ALLOWLISTED = "workspace_allowlisted"
    EXTERNAL_UNTRUSTED = "external_untrusted"


class IngestionMode(str, Enum):
    INGESTION_ONLY = "ingestion_only"
    PROPOSAL = "proposal"
    AUTO_VERIFIED = "auto_verified"


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        _json_value(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def fingerprint_json(value: Any) -> str:
    return "sha256:" + hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def fingerprint_bytes(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def validate_fingerprint(value: str, *, field_name: str = "fingerprint") -> str:
    if not isinstance(value, str) or not _FINGERPRINT_PATTERN.fullmatch(value):
        raise IngestionContractError(
            "invalid_fingerprint",
            f"{field_name} must be sha256:<64 lowercase hex>",
        )
    return value


def validate_safe_id(value: str, *, field_name: str) -> str:
    if (
        not isinstance(value, str)
        or not _SAFE_ID_PATTERN.fullmatch(value)
        or value in {".", ".."}
        or "/" in value
        or "\\" in value
    ):
        raise IngestionContractError(
            "unsafe_identity",
            f"{field_name} is not a safe stable identity",
        )
    return value


def validate_relative_path(value: str, *, field_name: str = "relative_path") -> str:
    if not isinstance(value, str) or not value or "\x00" in value:
        raise IngestionContractError("source_escape", f"{field_name} is invalid")
    path = PurePosixPath(value.replace("\\", "/"))
    if path.is_absolute() or ".." in path.parts or "." == str(path):
        raise IngestionContractError(
            "source_escape",
            f"{field_name} must stay inside the source root",
        )
    return path.as_posix()


def _non_negative_int(value: int, *, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise IngestionContractError(
            "invalid_count",
            f"{field_name} must be a non-negative integer",
        )
    return value


def _positive_int(value: int, *, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise IngestionContractError(
            "invalid_limit",
            f"{field_name} must be a positive integer",
        )
    return value


def _rate(value: float, *, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise IngestionContractError("invalid_rate", f"{field_name} must be numeric")
    normalized = float(value)
    if normalized < 0.0 or normalized > 1.0:
        raise IngestionContractError(
            "invalid_rate",
            f"{field_name} must be between 0 and 1",
        )
    return normalized


def _schema(payload: Mapping[str, Any], expected: str) -> None:
    if payload.get("schema_version") != expected:
        raise IngestionContractError(
            "schema_version_mismatch",
            f"expected schema_version={expected}",
        )


def _mapping(value: Any, *, field_name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise IngestionContractError(
            "schema_invalid",
            f"{field_name} must be an object",
        )
    return value


def _json_value(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if hasattr(value, "to_dict") and callable(value.to_dict):
        return value.to_dict()
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_json_value(item) for item in value]
    return value


@dataclass(frozen=True)
class IngestionLimits:
    max_files: int = 1000
    max_file_bytes: int = 16 * 1024 * 1024
    max_total_bytes: int = 256 * 1024 * 1024
    max_cases: int = 10_000
    max_asset_sample_bytes: int = 64 * 1024
    max_agent_sample_bytes: int = 512 * 1024
    max_mapping_candidates: int = 2
    max_representation_repairs: int = 2

    def __post_init__(self) -> None:
        for name in (
            "max_files",
            "max_file_bytes",
            "max_total_bytes",
            "max_cases",
            "max_asset_sample_bytes",
            "max_agent_sample_bytes",
            "max_mapping_candidates",
        ):
            _positive_int(getattr(self, name), field_name=name)
        _non_negative_int(
            self.max_representation_repairs,
            field_name="max_representation_repairs",
        )
        if self.max_file_bytes > self.max_total_bytes:
            raise IngestionContractError(
                "invalid_limit",
                "max_file_bytes cannot exceed max_total_bytes",
            )
        if self.max_asset_sample_bytes > self.max_agent_sample_bytes:
            raise IngestionContractError(
                "invalid_limit",
                "max_asset_sample_bytes cannot exceed max_agent_sample_bytes",
            )

    def to_dict(self) -> dict[str, int]:
        return {
            "max_files": self.max_files,
            "max_file_bytes": self.max_file_bytes,
            "max_total_bytes": self.max_total_bytes,
            "max_cases": self.max_cases,
            "max_asset_sample_bytes": self.max_asset_sample_bytes,
            "max_agent_sample_bytes": self.max_agent_sample_bytes,
            "max_mapping_candidates": self.max_mapping_candidates,
            "max_representation_repairs": self.max_representation_repairs,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "IngestionLimits":
        return cls(**dict(payload))


@dataclass(frozen=True)
class IngestionDiagnostic:
    reason_code: str
    asset_identity: str | None = None
    record_locator: str | None = None
    detail: str | None = None
    required: bool = False

    def __post_init__(self) -> None:
        if not re.fullmatch(r"[a-z][a-z0-9_]*", self.reason_code):
            raise IngestionContractError(
                "schema_invalid",
                "diagnostic reason_code must be lower_snake_case",
            )
        if self.asset_identity is not None:
            validate_fingerprint(self.asset_identity, field_name="asset_identity")
        if self.record_locator is not None and len(self.record_locator) > 512:
            raise IngestionContractError(
                "schema_invalid",
                "record locator exceeds 512 characters",
            )
        if self.detail is not None and len(self.detail) > 512:
            raise IngestionContractError(
                "schema_invalid",
                "diagnostic detail exceeds 512 characters",
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "reason_code": self.reason_code,
            "asset_identity": self.asset_identity,
            "record_locator": self.record_locator,
            "detail": self.detail,
            "required": self.required,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "IngestionDiagnostic":
        return cls(
            reason_code=str(payload.get("reason_code") or ""),
            asset_identity=payload.get("asset_identity"),
            record_locator=payload.get("record_locator"),
            detail=payload.get("detail"),
            required=bool(payload.get("required", False)),
        )


@dataclass(frozen=True)
class SourceAsset:
    asset_id: str
    relative_path: str
    media_type: str
    size_bytes: int
    content_fingerprint: str
    extractor_name: str | None = None
    extractor_version: str | None = None
    structural_profile: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        validate_fingerprint(self.asset_id, field_name="asset_id")
        object.__setattr__(
            self,
            "relative_path",
            validate_relative_path(self.relative_path),
        )
        validate_fingerprint(
            self.content_fingerprint,
            field_name="content_fingerprint",
        )
        _non_negative_int(self.size_bytes, field_name="size_bytes")
        if not isinstance(self.media_type, str) or not self.media_type:
            raise IngestionContractError(
                "schema_invalid",
                "media_type must be non-empty",
            )
        if self.extractor_name is not None and not _SAFE_NAME_PATTERN.fullmatch(
            self.extractor_name
        ):
            raise IngestionContractError(
                "schema_invalid",
                "extractor_name must be a stable lower-case name",
            )
        canonical_json_bytes(self.structural_profile)

    @staticmethod
    def identity_for(relative_path: str, content_fingerprint: str) -> str:
        return fingerprint_json(
            {
                "relative_path": validate_relative_path(relative_path),
                "content_fingerprint": validate_fingerprint(content_fingerprint),
            }
        )

    def verify_identity(self) -> None:
        if self.asset_id != self.identity_for(
            self.relative_path,
            self.content_fingerprint,
        ):
            raise IngestionContractError(
                "fingerprint_mismatch",
                "asset_id does not match locator and content",
            )

    def to_dict(self, *, public: bool = False) -> dict[str, Any]:
        result = {
            "asset_id": self.asset_id,
            "relative_path": self.relative_path,
            "media_type": self.media_type,
            "size_bytes": self.size_bytes,
            "content_fingerprint": self.content_fingerprint,
            "extractor_name": self.extractor_name,
            "extractor_version": self.extractor_version,
            "structural_profile": _json_value(self.structural_profile),
        }
        return result

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "SourceAsset":
        asset = cls(
            asset_id=str(payload.get("asset_id") or ""),
            relative_path=str(payload.get("relative_path") or ""),
            media_type=str(payload.get("media_type") or ""),
            size_bytes=payload.get("size_bytes"),
            content_fingerprint=str(payload.get("content_fingerprint") or ""),
            extractor_name=payload.get("extractor_name"),
            extractor_version=payload.get("extractor_version"),
            structural_profile=_mapping(
                payload.get("structural_profile", {}),
                field_name="structural_profile",
            ),
        )
        asset.verify_identity()
        return asset


@dataclass(frozen=True)
class SourceInventory:
    source_kind: SourceKind
    source_root_fingerprint: str
    assets: tuple[SourceAsset, ...]
    ignored_assets: tuple[IngestionDiagnostic, ...] = ()
    rejected_assets: tuple[IngestionDiagnostic, ...] = ()
    schema_version: str = SOURCE_INVENTORY_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != SOURCE_INVENTORY_SCHEMA_VERSION:
            raise IngestionContractError(
                "schema_version_mismatch",
                "invalid source inventory schema",
            )
        validate_fingerprint(
            self.source_root_fingerprint,
            field_name="source_root_fingerprint",
        )
        identities = [asset.asset_id for asset in self.assets]
        if len(identities) != len(set(identities)):
            raise IngestionContractError(
                "duplicate_identity",
                "source inventory contains duplicate asset identities",
            )
        paths = [asset.relative_path for asset in self.assets]
        if len(paths) != len(set(paths)):
            raise IngestionContractError(
                "duplicate_identity",
                "source inventory contains duplicate relative paths",
            )

    @staticmethod
    def fingerprint_for(
        source_kind: SourceKind,
        assets: Sequence[SourceAsset],
        ignored_assets: Sequence[IngestionDiagnostic] = (),
        rejected_assets: Sequence[IngestionDiagnostic] = (),
    ) -> str:
        return fingerprint_json(
            {
                "schema_version": SOURCE_INVENTORY_SCHEMA_VERSION,
                "source_kind": source_kind.value,
                "assets": [
                    asset.to_dict(public=False)
                    for asset in sorted(assets, key=lambda item: item.relative_path)
                ],
                "ignored_assets": [
                    item.to_dict()
                    for item in sorted(
                        ignored_assets,
                        key=lambda item: (
                            item.reason_code,
                            item.asset_identity or "",
                            item.record_locator or "",
                        ),
                    )
                ],
                "rejected_assets": [
                    item.to_dict()
                    for item in sorted(
                        rejected_assets,
                        key=lambda item: (
                            item.reason_code,
                            item.asset_identity or "",
                            item.record_locator or "",
                        ),
                    )
                ],
            }
        )

    @classmethod
    def create(
        cls,
        *,
        source_kind: SourceKind,
        assets: Sequence[SourceAsset],
        ignored_assets: Sequence[IngestionDiagnostic] = (),
        rejected_assets: Sequence[IngestionDiagnostic] = (),
    ) -> "SourceInventory":
        sorted_assets = tuple(sorted(assets, key=lambda item: item.relative_path))
        ignored = tuple(ignored_assets)
        rejected = tuple(rejected_assets)
        return cls(
            source_kind=source_kind,
            source_root_fingerprint=cls.fingerprint_for(
                source_kind,
                sorted_assets,
                ignored,
                rejected,
            ),
            assets=sorted_assets,
            ignored_assets=ignored,
            rejected_assets=rejected,
        )

    def verify_fingerprint(self) -> None:
        expected = self.fingerprint_for(
            self.source_kind,
            self.assets,
            self.ignored_assets,
            self.rejected_assets,
        )
        if self.source_root_fingerprint != expected:
            raise IngestionContractError(
                "fingerprint_mismatch",
                "source inventory fingerprint mismatch",
            )

    def to_dict(self, *, public: bool = False) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "source_kind": self.source_kind.value,
            "source_root_fingerprint": self.source_root_fingerprint,
            "assets": [asset.to_dict(public=public) for asset in self.assets],
            "ignored_assets": [item.to_dict() for item in self.ignored_assets],
            "rejected_assets": [item.to_dict() for item in self.rejected_assets],
        }

    def public_projection(self) -> dict[str, Any]:
        payload = self.to_dict(public=True)
        payload["asset_count"] = len(self.assets)
        return payload

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "SourceInventory":
        _schema(payload, SOURCE_INVENTORY_SCHEMA_VERSION)
        inventory = cls(
            schema_version=SOURCE_INVENTORY_SCHEMA_VERSION,
            source_kind=SourceKind(payload.get("source_kind")),
            source_root_fingerprint=str(
                payload.get("source_root_fingerprint") or ""
            ),
            assets=tuple(
                SourceAsset.from_dict(_mapping(item, field_name="asset"))
                for item in payload.get("assets", ())
            ),
            ignored_assets=tuple(
                IngestionDiagnostic.from_dict(
                    _mapping(item, field_name="ignored_asset")
                )
                for item in payload.get("ignored_assets", ())
            ),
            rejected_assets=tuple(
                IngestionDiagnostic.from_dict(
                    _mapping(item, field_name="rejected_asset")
                )
                for item in payload.get("rejected_assets", ())
            ),
        )
        inventory.verify_fingerprint()
        return inventory


@dataclass(frozen=True)
class ExtractedRecord:
    locator: str
    value: Any

    def __post_init__(self) -> None:
        if not isinstance(self.locator, str) or not self.locator:
            raise IngestionContractError(
                "schema_invalid",
                "record locator must be non-empty",
            )
        if len(self.locator) > 512:
            raise IngestionContractError(
                "schema_invalid",
                "record locator exceeds 512 characters",
            )
        canonical_json_bytes(self.value)

    def to_dict(self) -> dict[str, Any]:
        return {"locator": self.locator, "value": _json_value(self.value)}

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "ExtractedRecord":
        return cls(locator=str(payload.get("locator") or ""), value=payload.get("value"))


@dataclass(frozen=True)
class ExtractedDocument:
    asset_id: str
    media_type: str
    records: tuple[ExtractedRecord, ...]
    structural_profile: Mapping[str, Any]
    extractor_name: str
    extractor_version: str
    schema_version: str = EXTRACTED_DOCUMENT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != EXTRACTED_DOCUMENT_SCHEMA_VERSION:
            raise IngestionContractError(
                "schema_version_mismatch",
                "invalid extracted document schema",
            )
        validate_fingerprint(self.asset_id, field_name="asset_id")
        canonical_json_bytes(self.structural_profile)

    def to_dict(self, *, include_records: bool = True) -> dict[str, Any]:
        result = {
            "schema_version": self.schema_version,
            "asset_id": self.asset_id,
            "media_type": self.media_type,
            "structural_profile": _json_value(self.structural_profile),
            "extractor_name": self.extractor_name,
            "extractor_version": self.extractor_version,
            "record_count": len(self.records),
        }
        if include_records:
            result["records"] = [record.to_dict() for record in self.records]
        return result

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "ExtractedDocument":
        _schema(payload, EXTRACTED_DOCUMENT_SCHEMA_VERSION)
        return cls(
            asset_id=str(payload.get("asset_id") or ""),
            media_type=str(payload.get("media_type") or ""),
            records=tuple(
                ExtractedRecord.from_dict(_mapping(item, field_name="record"))
                for item in payload.get("records", ())
            ),
            structural_profile=_mapping(
                payload.get("structural_profile", {}),
                field_name="structural_profile",
            ),
            extractor_name=str(payload.get("extractor_name") or ""),
            extractor_version=str(payload.get("extractor_version") or ""),
        )


@dataclass(frozen=True)
class DatasetIngestionRequest:
    source_path: Path
    ingestor_name: str = "auto"
    manifest_path: Path | None = None
    model_profile: str | None = None
    limits: IngestionLimits = field(default_factory=IngestionLimits)
    mode: IngestionMode = IngestionMode.PROPOSAL
    schema_version: str = DATASET_INGESTION_REQUEST_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != DATASET_INGESTION_REQUEST_SCHEMA_VERSION:
            raise IngestionContractError(
                "schema_version_mismatch",
                "invalid ingestion request schema",
            )
        if not isinstance(self.source_path, Path):
            object.__setattr__(self, "source_path", Path(self.source_path))
        if self.manifest_path is not None and not isinstance(self.manifest_path, Path):
            object.__setattr__(self, "manifest_path", Path(self.manifest_path))
        if not _SAFE_NAME_PATTERN.fullmatch(self.ingestor_name):
            raise IngestionContractError(
                "unsafe_identity",
                "ingestor_name must be a registered stable name",
            )

    def to_dict(self, *, public: bool = False) -> dict[str, Any]:
        result = {
            "schema_version": self.schema_version,
            "ingestor_name": self.ingestor_name,
            "model_profile": self.model_profile,
            "limits": self.limits.to_dict(),
            "mode": self.mode.value,
            "has_manifest": self.manifest_path is not None,
        }
        if not public:
            result["source_path"] = str(self.source_path)
            result["manifest_path"] = (
                str(self.manifest_path) if self.manifest_path is not None else None
            )
        return result

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "DatasetIngestionRequest":
        _schema(payload, DATASET_INGESTION_REQUEST_SCHEMA_VERSION)
        source_path = payload.get("source_path")
        if not isinstance(source_path, str) or not source_path:
            raise IngestionContractError(
                "schema_invalid",
                "source_path is required in private request serialization",
            )
        manifest_path = payload.get("manifest_path")
        return cls(
            source_path=Path(source_path),
            ingestor_name=str(payload.get("ingestor_name") or "auto"),
            manifest_path=Path(manifest_path) if manifest_path else None,
            model_profile=payload.get("model_profile"),
            limits=IngestionLimits.from_dict(
                _mapping(payload.get("limits", {}), field_name="limits")
            ),
            mode=IngestionMode(payload.get("mode", IngestionMode.PROPOSAL.value)),
        )


@dataclass(frozen=True)
class AssetSelector:
    name: str
    include: tuple[str, ...] = ("**/*",)
    exclude: tuple[str, ...] = ()
    media_types: tuple[str, ...] = ()
    required: bool = True

    def __post_init__(self) -> None:
        if not _SAFE_NAME_PATTERN.fullmatch(self.name):
            raise IngestionContractError(
                "mapping_protocol_invalid",
                "asset selector name must be lower-case and stable",
            )
        for pattern in (*self.include, *self.exclude):
            if (
                not isinstance(pattern, str)
                or not pattern
                or "\x00" in pattern
                or PurePosixPath(pattern).is_absolute()
                or ".." in PurePosixPath(pattern).parts
            ):
                raise IngestionContractError(
                    "source_escape",
                    "asset selector glob must stay inside source root",
                )

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "include": list(self.include),
            "exclude": list(self.exclude),
            "media_types": list(self.media_types),
            "required": self.required,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "AssetSelector":
        return cls(
            name=str(payload.get("name") or ""),
            include=tuple(str(item) for item in payload.get("include", ("**/*",))),
            exclude=tuple(str(item) for item in payload.get("exclude", ())),
            media_types=tuple(str(item) for item in payload.get("media_types", ())),
            required=bool(payload.get("required", True)),
        )


@dataclass(frozen=True)
class RecordFramingSpec:
    kind: str
    asset_selector: str | None = None
    delimiter: str | None = None

    def __post_init__(self) -> None:
        allowed = {
            "json_object",
            "json_array",
            "jsonl_rows",
            "csv_rows",
            "yaml_object",
            "yaml_array",
            "one_file_per_case",
            "literal_delimited_blocks",
        }
        if self.kind not in allowed:
            raise IngestionContractError(
                "mapping_capability_not_supported",
                f"unsupported record framing: {self.kind}",
            )
        if self.kind == "literal_delimited_blocks":
            if not self.delimiter or len(self.delimiter) > 128:
                raise IngestionContractError(
                    "mapping_protocol_invalid",
                    "literal_delimited_blocks requires a bounded delimiter",
                )

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "asset_selector": self.asset_selector,
            "delimiter": self.delimiter,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "RecordFramingSpec":
        return cls(
            kind=str(payload.get("kind") or payload.get("framing") or ""),
            asset_selector=payload.get("asset_selector"),
            delimiter=payload.get("delimiter"),
        )


@dataclass(frozen=True)
class FieldMapping:
    source: str | None = None
    sources: tuple[str, ...] = ()
    transform: str = "identity"
    constant: Any = None
    required: bool = False

    def __post_init__(self) -> None:
        allowed_transforms = {
            "identity",
            "stringify",
            "parse_json",
            "coalesce",
            "bounded_join",
            "status_map",
            "constant_from_manifest",
        }
        if self.transform not in allowed_transforms:
            raise IngestionContractError(
                "mapping_capability_not_supported",
                f"unsupported transform: {self.transform}",
            )
        selectors = tuple(item for item in (self.source, *self.sources) if item)
        for selector in selectors:
            if not _valid_field_selector(selector):
                raise IngestionContractError(
                    "mapping_protocol_invalid",
                    f"invalid field selector: {selector}",
                )
        if self.transform == "constant_from_manifest" and self.constant is None:
            raise IngestionContractError(
                "mapping_protocol_invalid",
                "constant_from_manifest requires a constant",
            )
        canonical_json_bytes(self.constant)

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "sources": list(self.sources),
            "transform": self.transform,
            "constant": _json_value(self.constant),
            "required": self.required,
        }

    @classmethod
    def from_value(cls, payload: Any) -> "FieldMapping":
        if isinstance(payload, str):
            return cls(source=payload)
        mapping = _mapping(payload, field_name="field mapping")
        source = mapping.get("source", mapping.get("from"))
        sources = mapping.get("sources", ())
        if isinstance(sources, str):
            sources = (sources,)
        return cls(
            source=str(source) if source is not None else None,
            sources=tuple(str(item) for item in sources),
            transform=str(mapping.get("transform") or "identity"),
            constant=mapping.get("constant"),
            required=bool(mapping.get("required", False)),
        )


@dataclass(frozen=True)
class CaseFieldMappings:
    input: FieldMapping
    case_id: FieldMapping | None = None
    expected_output: FieldMapping | None = None
    metadata: FieldMapping | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id.to_dict() if self.case_id else None,
            "input": self.input.to_dict(),
            "expected_output": (
                self.expected_output.to_dict() if self.expected_output else None
            ),
            "metadata": self.metadata.to_dict() if self.metadata else None,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "CaseFieldMappings":
        if "verification_command" in payload:
            raise IngestionContractError(
                "generated_command_not_allowed",
                "mapping specs cannot generate verification commands",
            )
        if "input" not in payload:
            raise IngestionContractError(
                "mapping_protocol_invalid",
                "input field mapping is required",
            )
        return cls(
            case_id=(
                FieldMapping.from_value(payload["case_id"])
                if payload.get("case_id") is not None
                else None
            ),
            input=FieldMapping.from_value(payload["input"]),
            expected_output=(
                FieldMapping.from_value(payload["expected_output"])
                if payload.get("expected_output") is not None
                else None
            ),
            metadata=(
                FieldMapping.from_value(payload["metadata"])
                if payload.get("metadata") is not None
                else None
            ),
        )


@dataclass(frozen=True)
class JoinSpec:
    left_asset: str
    left_key: str
    right_asset: str
    right_key: str
    join_type: str = "inner"
    cardinality: str = "one_to_one"
    unmatched_policy: str = "reject"
    required: bool = True

    def __post_init__(self) -> None:
        if self.join_type not in {"inner", "left"}:
            raise IngestionContractError(
                "mapping_capability_not_supported",
                "only inner and left joins are supported",
            )
        if self.cardinality not in {"one_to_one", "many_to_one", "one_to_many"}:
            raise IngestionContractError(
                "mapping_capability_not_supported",
                "unsupported join cardinality",
            )
        if self.unmatched_policy not in {"reject", "allow"}:
            raise IngestionContractError(
                "mapping_protocol_invalid",
                "unmatched_policy must be reject or allow",
            )
        for value in (self.left_key, self.right_key):
            if not _valid_field_selector(value):
                raise IngestionContractError(
                    "mapping_protocol_invalid",
                    "join keys must be declarative field selectors",
                )

    def to_dict(self) -> dict[str, Any]:
        return {
            "left_asset": self.left_asset,
            "left_key": self.left_key,
            "right_asset": self.right_asset,
            "right_key": self.right_key,
            "join_type": self.join_type,
            "cardinality": self.cardinality,
            "unmatched_policy": self.unmatched_policy,
            "required": self.required,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "JoinSpec":
        left = str(payload.get("left_key") or payload.get("left") or "")
        right = str(payload.get("right_key") or payload.get("right") or "")
        left_asset = str(payload.get("left_asset") or "")
        right_asset = str(payload.get("right_asset") or "")
        if not left_asset and "." in left:
            left_asset, left = left.split(".", 1)
        if not right_asset and "." in right:
            right_asset, right = right.split(".", 1)
        return cls(
            left_asset=left_asset,
            left_key=left,
            right_asset=right_asset,
            right_key=right,
            join_type=str(payload.get("join_type") or "inner"),
            cardinality=str(payload.get("cardinality") or "one_to_one"),
            unmatched_policy=str(payload.get("unmatched_policy") or "reject"),
            required=bool(payload.get("required", True)),
        )


@dataclass(frozen=True)
class TrajectoryStepMappings:
    id: FieldMapping | None = None
    meta: FieldMapping | None = None
    state: FieldMapping | None = None
    action: FieldMapping | None = None
    reward: FieldMapping | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            name: value.to_dict() if value else None
            for name, value in (
                ("id", self.id),
                ("meta", self.meta),
                ("state", self.state),
                ("action", self.action),
                ("reward", self.reward),
            )
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "TrajectoryStepMappings":
        return cls(
            **{
                name: (
                    FieldMapping.from_value(payload[name])
                    if payload.get(name) is not None
                    else None
                )
                for name in ("id", "meta", "state", "action", "reward")
            }
        )


@dataclass(frozen=True)
class TrajectoryMappingSpec:
    steps: FieldMapping
    task_id: FieldMapping | None = None
    step_fields: TrajectoryStepMappings = field(
        default_factory=TrajectoryStepMappings
    )
    status_map: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for key, value in self.status_map.items():
            if not isinstance(key, str) or not isinstance(value, str):
                raise IngestionContractError(
                    "mapping_protocol_invalid",
                    "trajectory status_map must map strings to strings",
                )

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id.to_dict() if self.task_id else None,
            "steps": self.steps.to_dict(),
            "step_fields": self.step_fields.to_dict(),
            "status_map": dict(sorted(self.status_map.items())),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "TrajectoryMappingSpec":
        if payload.get("steps") is None:
            raise IngestionContractError(
                "mapping_protocol_invalid",
                "trajectory steps mapping is required",
            )
        return cls(
            task_id=(
                FieldMapping.from_value(payload["task_id"])
                if payload.get("task_id") is not None
                else None
            ),
            steps=FieldMapping.from_value(payload["steps"]),
            step_fields=TrajectoryStepMappings.from_dict(
                _mapping(payload.get("step_fields", {}), field_name="step_fields")
            ),
            status_map={
                str(key): str(value)
                for key, value in _mapping(
                    payload.get("status_map", {}),
                    field_name="status_map",
                ).items()
            },
        )


@dataclass(frozen=True)
class DeclaredExclusion:
    asset_selector: str
    structural_reason: str

    def __post_init__(self) -> None:
        forbidden = {
            "expected_output",
            "answer",
            "label",
            "score",
            "reward",
            "outcome",
            "judge",
            "candidate",
        }
        lowered = self.structural_reason.lower()
        if any(token in lowered for token in forbidden):
            raise IngestionContractError(
                "outcome_based_exclusion_not_allowed",
                "exclusions may only use structural reasons",
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "asset_selector": self.asset_selector,
            "structural_reason": self.structural_reason,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "DeclaredExclusion":
        return cls(
            asset_selector=str(payload.get("asset_selector") or ""),
            structural_reason=str(
                payload.get("structural_reason") or payload.get("reason") or ""
            ),
        )


@dataclass(frozen=True)
class DatasetMappingSpec:
    mapping_id: str
    asset_selectors: tuple[AssetSelector, ...]
    record_framing: RecordFramingSpec
    fields: CaseFieldMappings
    joins: tuple[JoinSpec, ...] = ()
    trajectory: TrajectoryMappingSpec | None = None
    declared_exclusions: tuple[DeclaredExclusion, ...] = ()
    rationale_codes: tuple[str, ...] = ()
    agent_confidence: float | None = None
    schema_version: str = DATASET_MAPPING_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != DATASET_MAPPING_SCHEMA_VERSION:
            raise IngestionContractError(
                "schema_version_mismatch",
                "invalid dataset mapping schema",
            )
        validate_safe_id(self.mapping_id, field_name="mapping_id")
        names = [item.name for item in self.asset_selectors]
        if len(names) != len(set(names)):
            raise IngestionContractError(
                "duplicate_identity",
                "asset selector names must be unique",
            )
        for join in self.joins:
            if join.left_asset not in names or join.right_asset not in names:
                raise IngestionContractError(
                    "mapping_protocol_invalid",
                    "join references an unknown asset selector",
                )
        if (
            self.record_framing.asset_selector is not None
            and self.record_framing.asset_selector not in names
        ):
            raise IngestionContractError(
                "mapping_protocol_invalid",
                "record framing references an unknown asset selector",
            )
        for reason in self.rationale_codes:
            if not re.fullmatch(r"[a-z][a-z0-9_]*", reason):
                raise IngestionContractError(
                    "mapping_protocol_invalid",
                    "rationale codes must be lower_snake_case",
                )
        if self.agent_confidence is not None:
            object.__setattr__(
                self,
                "agent_confidence",
                _rate(self.agent_confidence, field_name="agent_confidence"),
            )

    @property
    def fingerprint(self) -> str:
        payload = self.to_dict()
        payload.pop("agent_confidence", None)
        payload.pop("mapping_id", None)
        return fingerprint_json(payload)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "mapping_id": self.mapping_id,
            "asset_selectors": [item.to_dict() for item in self.asset_selectors],
            "record_framing": self.record_framing.to_dict(),
            "joins": [item.to_dict() for item in self.joins],
            "fields": self.fields.to_dict(),
            "trajectory": self.trajectory.to_dict() if self.trajectory else None,
            "declared_exclusions": [
                item.to_dict() for item in self.declared_exclusions
            ],
            "rationale": {"codes": list(self.rationale_codes)},
            "agent_confidence": self.agent_confidence,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "DatasetMappingSpec":
        _schema(payload, DATASET_MAPPING_SCHEMA_VERSION)
        forbidden_keys = {
            "code",
            "python",
            "shell",
            "command",
            "verification_command",
            "template",
            "jinja",
            "regex",
            "import",
            "module",
            "callable",
            "subprocess",
        }
        _reject_forbidden_keys(payload, forbidden_keys)
        rationale = payload.get("rationale", {})
        if isinstance(rationale, Mapping):
            codes = rationale.get("codes", ())
        elif isinstance(rationale, Sequence) and not isinstance(
            rationale, (str, bytes)
        ):
            codes = rationale
        else:
            codes = ()
        return cls(
            schema_version=DATASET_MAPPING_SCHEMA_VERSION,
            mapping_id=str(payload.get("mapping_id") or ""),
            asset_selectors=tuple(
                AssetSelector.from_dict(_mapping(item, field_name="asset_selector"))
                for item in payload.get("asset_selectors", ())
            ),
            record_framing=RecordFramingSpec.from_dict(
                _mapping(
                    payload.get("record_framing", {}),
                    field_name="record_framing",
                )
            ),
            joins=tuple(
                JoinSpec.from_dict(_mapping(item, field_name="join"))
                for item in payload.get("joins", ())
            ),
            fields=CaseFieldMappings.from_dict(
                _mapping(payload.get("fields", {}), field_name="fields")
            ),
            trajectory=(
                TrajectoryMappingSpec.from_dict(
                    _mapping(payload["trajectory"], field_name="trajectory")
                )
                if payload.get("trajectory") is not None
                else None
            ),
            declared_exclusions=tuple(
                DeclaredExclusion.from_dict(
                    _mapping(item, field_name="declared_exclusion")
                )
                for item in payload.get("declared_exclusions", ())
            ),
            rationale_codes=tuple(str(item) for item in codes),
            agent_confidence=payload.get("agent_confidence"),
        )


@dataclass(frozen=True)
class CaseSourceProvenance:
    asset_ids: tuple[str, ...]
    record_locators: tuple[str, ...]
    mapping_fingerprint: str
    ingestion_id: str | None = None
    verification_origin: str | None = None

    def __post_init__(self) -> None:
        if not self.asset_ids:
            raise IngestionContractError(
                "provenance_missing",
                "at least one source asset is required",
            )
        for asset_id in self.asset_ids:
            validate_fingerprint(asset_id, field_name="asset_id")
        if len(self.asset_ids) != len(set(self.asset_ids)):
            raise IngestionContractError(
                "duplicate_identity",
                "provenance contains duplicate asset identities",
            )
        if len(self.asset_ids) != len(self.record_locators):
            raise IngestionContractError(
                "provenance_missing",
                "asset identities and locators must align",
            )
        validate_fingerprint(
            self.mapping_fingerprint,
            field_name="mapping_fingerprint",
        )
        if self.ingestion_id is not None:
            validate_safe_id(self.ingestion_id, field_name="ingestion_id")
        if self.verification_origin not in {
            None,
            "user_manifest",
            "trusted_registered_ingestor",
        }:
            raise IngestionContractError(
                "verification_provenance_invalid",
                "verification command origin is not trusted",
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "ingestion_id": self.ingestion_id,
            "asset_ids": list(self.asset_ids),
            "record_locators": list(self.record_locators),
            "mapping_fingerprint": self.mapping_fingerprint,
            "verification_origin": self.verification_origin,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "CaseSourceProvenance":
        return cls(
            ingestion_id=payload.get("ingestion_id"),
            asset_ids=tuple(str(item) for item in payload.get("asset_ids", ())),
            record_locators=tuple(
                str(item) for item in payload.get("record_locators", ())
            ),
            mapping_fingerprint=str(
                payload.get("mapping_fingerprint") or ""
            ),
            verification_origin=payload.get("verification_origin"),
        )


@dataclass(frozen=True)
class NormalizedCaseRecord:
    case_id: str
    input: Any
    source: CaseSourceProvenance
    expected_output: Any = None
    verification_command: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)
    trajectory: Mapping[str, Any] | None = None
    trace_replayability: str = "absent"
    schema_version: str = NORMALIZED_CASE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != NORMALIZED_CASE_SCHEMA_VERSION:
            raise IngestionContractError(
                "schema_version_mismatch",
                "invalid normalized case schema",
            )
        validate_safe_id(self.case_id, field_name="case_id")
        if self.input is None:
            raise IngestionContractError(
                "input_missing",
                "normalized case input cannot be null",
            )
        if self.verification_command is not None:
            if self.source.verification_origin not in {
                "user_manifest",
                "trusted_registered_ingestor",
            }:
                raise IngestionContractError(
                    "generated_command_not_allowed",
                    "verification command lacks trusted provenance",
                )
        elif self.source.verification_origin is not None:
            raise IngestionContractError(
                "verification_provenance_invalid",
                "verification provenance exists without a command",
            )
        if self.trace_replayability not in {"absent", "incomplete", "replayable"}:
            raise IngestionContractError(
                "schema_invalid",
                "trace_replayability must be absent, incomplete, or replayable",
            )
        canonical_json_bytes(self.input)
        canonical_json_bytes(self.expected_output)
        canonical_json_bytes(self.metadata)
        canonical_json_bytes(self.trajectory)

    @property
    def fingerprint(self) -> str:
        return fingerprint_json(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "case_id": self.case_id,
            "input": _json_value(self.input),
            "expected_output": _json_value(self.expected_output),
            "verification_command": self.verification_command,
            "metadata": _json_value(self.metadata),
            "trajectory": _json_value(self.trajectory),
            "trace_replayability": self.trace_replayability,
            "source": self.source.to_dict(),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "NormalizedCaseRecord":
        _schema(payload, NORMALIZED_CASE_SCHEMA_VERSION)
        return cls(
            case_id=str(payload.get("case_id") or ""),
            input=payload.get("input"),
            expected_output=payload.get("expected_output"),
            verification_command=payload.get("verification_command"),
            metadata=_mapping(payload.get("metadata", {}), field_name="metadata"),
            trajectory=(
                _mapping(payload["trajectory"], field_name="trajectory")
                if payload.get("trajectory") is not None
                else None
            ),
            trace_replayability=str(
                payload.get("trace_replayability") or "absent"
            ),
            source=CaseSourceProvenance.from_dict(
                _mapping(payload.get("source", {}), field_name="source")
            ),
        )


@dataclass(frozen=True)
class RejectedRecord:
    reason_code: str
    asset_id: str
    record_locator: str
    detail: str | None = None
    schema_version: str = REJECTED_RECORD_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != REJECTED_RECORD_SCHEMA_VERSION:
            raise IngestionContractError(
                "schema_version_mismatch",
                "invalid rejected record schema",
            )
        if not re.fullmatch(r"[a-z][a-z0-9_]*", self.reason_code):
            raise IngestionContractError(
                "schema_invalid",
                "rejected record reason must be lower_snake_case",
            )
        validate_fingerprint(self.asset_id, field_name="asset_id")
        if not self.record_locator or len(self.record_locator) > 512:
            raise IngestionContractError(
                "schema_invalid",
                "rejected record locator is invalid",
            )
        if self.detail is not None and len(self.detail) > 512:
            raise IngestionContractError(
                "schema_invalid",
                "rejected record detail exceeds 512 characters",
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "reason_code": self.reason_code,
            "asset_id": self.asset_id,
            "record_locator": self.record_locator,
            "detail": self.detail,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "RejectedRecord":
        _schema(payload, REJECTED_RECORD_SCHEMA_VERSION)
        return cls(
            reason_code=str(payload.get("reason_code") or ""),
            asset_id=str(payload.get("asset_id") or ""),
            record_locator=str(payload.get("record_locator") or ""),
            detail=payload.get("detail"),
        )


@dataclass(frozen=True)
class IngestionQualityReport:
    discovered_asset_count: int
    supported_asset_count: int
    ignored_asset_count: int
    rejected_asset_count: int
    total_source_bytes: int
    mapping_candidate_count: int
    valid_mapping_candidate_count: int
    selected_mapping_fingerprint: str
    eligible_record_count: int
    normalized_case_count: int
    rejected_record_count: int
    record_coverage_rate: float
    required_asset_coverage_rate: float
    input_present_rate: float
    expected_output_present_rate: float
    verification_present_rate: float
    trace_present_rate: float
    trace_replayable_rate: float
    duplicate_case_id_count: int
    case_id_stability: bool
    source_fingerprint: str
    normalized_dataset_fingerprint: str
    required_join_count: int = 0
    unmatched_required_join_count: int = 0
    join_cardinality_violation_count: int = 0
    deterministic_replay_match: bool = False
    mapping_execution_count: int = 0
    source_escape_count: int = 0
    symlink_rejection_count: int = 0
    generated_executable_count: int = 0
    generated_command_count: int = 0
    held_out_value_exposure_count: int = 0
    unknown_status_count: int = 0
    terminal_status_coverage_rate: float = 0.0
    state_input_coverage_rate: float = 0.0
    tool_call_structure_rate: float = 0.0
    unrecovered_failure_count: int = 0
    recovered_path_count: int = 0
    repeated_action_loop_count: int = 0
    no_recovery_opportunity_count: int = 0
    agent_confidence: float | None = None
    warning_reason_codes: tuple[str, ...] = ()
    failure_reason_codes: tuple[str, ...] = ()
    schema_version: str = INGESTION_QUALITY_REPORT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != INGESTION_QUALITY_REPORT_SCHEMA_VERSION:
            raise IngestionContractError(
                "schema_version_mismatch",
                "invalid ingestion quality report schema",
            )
        count_fields = (
            "discovered_asset_count",
            "supported_asset_count",
            "ignored_asset_count",
            "rejected_asset_count",
            "total_source_bytes",
            "mapping_candidate_count",
            "valid_mapping_candidate_count",
            "eligible_record_count",
            "normalized_case_count",
            "rejected_record_count",
            "duplicate_case_id_count",
            "required_join_count",
            "unmatched_required_join_count",
            "join_cardinality_violation_count",
            "mapping_execution_count",
            "source_escape_count",
            "symlink_rejection_count",
            "generated_executable_count",
            "generated_command_count",
            "held_out_value_exposure_count",
            "unknown_status_count",
            "unrecovered_failure_count",
            "recovered_path_count",
            "repeated_action_loop_count",
            "no_recovery_opportunity_count",
        )
        for name in count_fields:
            _non_negative_int(getattr(self, name), field_name=name)
        for name in (
            "record_coverage_rate",
            "required_asset_coverage_rate",
            "input_present_rate",
            "expected_output_present_rate",
            "verification_present_rate",
            "trace_present_rate",
            "trace_replayable_rate",
            "terminal_status_coverage_rate",
            "state_input_coverage_rate",
            "tool_call_structure_rate",
        ):
            object.__setattr__(
                self,
                name,
                _rate(getattr(self, name), field_name=name),
            )
        if self.agent_confidence is not None:
            object.__setattr__(
                self,
                "agent_confidence",
                _rate(self.agent_confidence, field_name="agent_confidence"),
            )
        for name in (
            "selected_mapping_fingerprint",
            "source_fingerprint",
            "normalized_dataset_fingerprint",
        ):
            validate_fingerprint(getattr(self, name), field_name=name)
        for reason in (*self.warning_reason_codes, *self.failure_reason_codes):
            if not re.fullmatch(r"[a-z][a-z0-9_]*", reason):
                raise IngestionContractError(
                    "schema_invalid",
                    "quality report reasons must be lower_snake_case",
                )

    def to_dict(self, *, public: bool = False) -> dict[str, Any]:
        result = {
            name: _json_value(getattr(self, name))
            for name in self.__dataclass_fields__
        }
        if public:
            result.pop("agent_confidence", None)
        return result

    def public_projection(self) -> dict[str, Any]:
        return self.to_dict(public=True)

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "IngestionQualityReport":
        _schema(payload, INGESTION_QUALITY_REPORT_SCHEMA_VERSION)
        kwargs = {
            name: payload[name]
            for name in cls.__dataclass_fields__
            if name in payload and name != "schema_version"
        }
        for name in ("warning_reason_codes", "failure_reason_codes"):
            if name in kwargs:
                kwargs[name] = tuple(str(item) for item in kwargs[name])
        return cls(**kwargs)


@dataclass(frozen=True)
class FrozenIngestionSnapshot:
    ingestion_id: str
    inventory: SourceInventory
    selected_mapping: DatasetMappingSpec
    normalized_cases: tuple[NormalizedCaseRecord, ...]
    rejected_records: tuple[RejectedRecord, ...]
    quality_report: IngestionQualityReport
    manifest_fingerprint: str | None = None
    source_manifest: Mapping[str, Any] | None = None
    extractor_fingerprints: tuple[str, ...] = ()
    mapping_candidates: tuple[DatasetMappingSpec, ...] = ()
    mapping_failures: tuple[Mapping[str, Any], ...] = ()
    ingestion_model_call_count: int = 0
    split_fingerprint: str | None = None
    ingestor_name: str = "auto"
    ingestor_version: str = "1"
    ingestor_trust_level: IngestorTrustLevel = (
        IngestorTrustLevel.FRAMEWORK_BUILTIN
    )
    schema_version: str = FROZEN_INGESTION_SNAPSHOT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != FROZEN_INGESTION_SNAPSHOT_SCHEMA_VERSION:
            raise IngestionContractError(
                "schema_version_mismatch",
                "invalid frozen ingestion snapshot schema",
            )
        validate_safe_id(self.ingestion_id, field_name="ingestion_id")
        if not _SAFE_NAME_PATTERN.fullmatch(self.ingestor_name):
            raise IngestionContractError(
                "unsafe_identity",
                "ingestor_name must be a registered stable name",
            )
        if not isinstance(self.ingestor_version, str) or not self.ingestor_version:
            raise IngestionContractError(
                "schema_invalid",
                "ingestor_version must be non-empty",
            )
        object.__setattr__(
            self,
            "ingestor_trust_level",
            IngestorTrustLevel(self.ingestor_trust_level),
        )
        if self.manifest_fingerprint is not None:
            validate_fingerprint(
                self.manifest_fingerprint,
                field_name="manifest_fingerprint",
            )
        if self.source_manifest is not None:
            canonical_json_bytes(self.source_manifest)
            if self.manifest_fingerprint != fingerprint_json(self.source_manifest):
                raise IngestionContractError(
                    "fingerprint_mismatch",
                    "source manifest fingerprint mismatch",
                )
        elif self.manifest_fingerprint is not None:
            raise IngestionContractError(
                "provenance_missing",
                "manifest fingerprint requires a frozen private manifest",
            )
        for fingerprint in self.extractor_fingerprints:
            validate_fingerprint(
                fingerprint,
                field_name="extractor_fingerprint",
            )
        if self.mapping_candidates and self.selected_mapping.fingerprint not in {
            candidate.fingerprint for candidate in self.mapping_candidates
        }:
            raise IngestionContractError(
                "provenance_missing",
                "selected mapping is absent from the frozen mapping population",
            )
        for failure in self.mapping_failures:
            canonical_json_bytes(failure)
        _non_negative_int(
            self.ingestion_model_call_count,
            field_name="ingestion_model_call_count",
        )
        if self.split_fingerprint is not None:
            validate_fingerprint(
                self.split_fingerprint,
                field_name="split_fingerprint",
            )
        case_ids = [case.case_id for case in self.normalized_cases]
        if len(case_ids) != len(set(case_ids)):
            raise IngestionContractError(
                "duplicate_case_identity",
                "frozen snapshot contains duplicate case ids",
            )
        if (
            self.quality_report.normalized_dataset_fingerprint
            != self.normalized_dataset_fingerprint
        ):
            raise IngestionContractError(
                "fingerprint_mismatch",
                "normalized dataset fingerprint mismatch",
            )
        if (
            self.quality_report.source_fingerprint
            != self.inventory.source_root_fingerprint
        ):
            raise IngestionContractError(
                "fingerprint_mismatch",
                "source fingerprint mismatch",
            )
        if (
            self.quality_report.selected_mapping_fingerprint
            != self.selected_mapping.fingerprint
        ):
            raise IngestionContractError(
                "fingerprint_mismatch",
                "selected mapping fingerprint mismatch",
            )
        if re.fullmatch(r"ingestion-[0-9a-f]{32}", self.ingestion_id):
            expected_identity = self.identity_for(
                inventory_fingerprint=self.inventory.source_root_fingerprint,
                mapping_fingerprint=self.selected_mapping.fingerprint,
                manifest_fingerprint=self.manifest_fingerprint,
                extractor_fingerprints=self.extractor_fingerprints,
                ingestor_name=self.ingestor_name,
                ingestor_version=self.ingestor_version,
                trust_level=self.ingestor_trust_level,
            )
            if self.ingestion_id != expected_identity:
                raise IngestionContractError(
                    "fingerprint_mismatch",
                    "ingestion identity does not match its frozen inputs",
                )

    @property
    def normalized_dataset_fingerprint(self) -> str:
        return fingerprint_json(
            [case.to_dict() for case in sorted(self.normalized_cases, key=lambda c: c.case_id)]
        )

    @staticmethod
    def identity_for(
        *,
        inventory_fingerprint: str,
        mapping_fingerprint: str,
        manifest_fingerprint: str | None,
        extractor_fingerprints: Sequence[str],
        ingestor_name: str,
        ingestor_version: str,
        trust_level: IngestorTrustLevel,
    ) -> str:
        digest = fingerprint_json(
            {
                "schema_version": FROZEN_INGESTION_SNAPSHOT_SCHEMA_VERSION,
                "inventory_fingerprint": validate_fingerprint(
                    inventory_fingerprint
                ),
                "mapping_fingerprint": validate_fingerprint(mapping_fingerprint),
                "manifest_fingerprint": manifest_fingerprint,
                "extractor_fingerprints": sorted(extractor_fingerprints),
                "ingestor_name": ingestor_name,
                "ingestor_version": ingestor_version,
                "trust_level": trust_level.value,
            }
        )
        return "ingestion-" + digest.removeprefix("sha256:")[:32]

    def to_dict(self, *, public: bool = False) -> dict[str, Any]:
        result = {
            "schema_version": self.schema_version,
            "ingestion_id": self.ingestion_id,
            "inventory": self.inventory.to_dict(public=public),
            "selected_mapping": self.selected_mapping.to_dict(),
            "normalized_dataset_fingerprint": self.normalized_dataset_fingerprint,
            "manifest_fingerprint": self.manifest_fingerprint,
            "extractor_fingerprints": list(self.extractor_fingerprints),
            "split_fingerprint": self.split_fingerprint,
            "ingestor_name": self.ingestor_name,
            "ingestor_version": self.ingestor_version,
            "ingestor_trust_level": self.ingestor_trust_level.value,
            "ingestion_model_call_count": self.ingestion_model_call_count,
            "quality_report": self.quality_report.to_dict(public=public),
        }
        if not public:
            result["source_manifest"] = (
                _json_value(self.source_manifest)
                if self.source_manifest is not None
                else None
            )
            result["normalized_cases"] = [
                case.to_dict() for case in self.normalized_cases
            ]
            result["rejected_records"] = [
                record.to_dict() for record in self.rejected_records
            ]
            result["mapping_candidates"] = [
                candidate.to_dict() for candidate in self.mapping_candidates
            ]
            result["mapping_failures"] = [
                _json_value(failure) for failure in self.mapping_failures
            ]
        else:
            result["normalized_case_count"] = len(self.normalized_cases)
            result["rejected_record_count"] = len(self.rejected_records)
        return result

    def public_projection(self) -> dict[str, Any]:
        return self.to_dict(public=True)

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "FrozenIngestionSnapshot":
        _schema(payload, FROZEN_INGESTION_SNAPSHOT_SCHEMA_VERSION)
        snapshot = cls(
            ingestion_id=str(payload.get("ingestion_id") or ""),
            inventory=SourceInventory.from_dict(
                _mapping(payload.get("inventory", {}), field_name="inventory")
            ),
            selected_mapping=DatasetMappingSpec.from_dict(
                _mapping(
                    payload.get("selected_mapping", {}),
                    field_name="selected_mapping",
                )
            ),
            normalized_cases=tuple(
                NormalizedCaseRecord.from_dict(
                    _mapping(item, field_name="normalized_case")
                )
                for item in payload.get("normalized_cases", ())
            ),
            rejected_records=tuple(
                RejectedRecord.from_dict(
                    _mapping(item, field_name="rejected_record")
                )
                for item in payload.get("rejected_records", ())
            ),
            quality_report=IngestionQualityReport.from_dict(
                _mapping(
                    payload.get("quality_report", {}),
                    field_name="quality_report",
                )
            ),
            manifest_fingerprint=payload.get("manifest_fingerprint"),
            source_manifest=(
                _mapping(
                    payload["source_manifest"],
                    field_name="source_manifest",
                )
                if payload.get("source_manifest") is not None
                else None
            ),
            extractor_fingerprints=tuple(
                str(item) for item in payload.get("extractor_fingerprints", ())
            ),
            mapping_candidates=tuple(
                DatasetMappingSpec.from_dict(
                    _mapping(item, field_name="mapping_candidate")
                )
                for item in payload.get("mapping_candidates", ())
            ),
            mapping_failures=tuple(
                _mapping(item, field_name="mapping_failure")
                for item in payload.get("mapping_failures", ())
            ),
            ingestion_model_call_count=payload.get(
                "ingestion_model_call_count",
                0,
            ),
            split_fingerprint=payload.get("split_fingerprint"),
            ingestor_name=str(payload.get("ingestor_name") or "auto"),
            ingestor_version=str(payload.get("ingestor_version") or "1"),
            ingestor_trust_level=IngestorTrustLevel(
                payload.get(
                    "ingestor_trust_level",
                    IngestorTrustLevel.FRAMEWORK_BUILTIN.value,
                )
            ),
        )
        claimed = payload.get("normalized_dataset_fingerprint")
        if (
            claimed is not None
            and claimed != snapshot.normalized_dataset_fingerprint
        ):
            raise IngestionContractError(
                "fingerprint_mismatch",
                "snapshot dataset fingerprint mismatch",
            )
        return snapshot


@runtime_checkable
class DatasetExtractor(Protocol):
    name: str
    version: str

    def supports(self, asset: SourceAsset) -> bool: ...

    def extract(
        self,
        asset_path: Path,
        *,
        asset: SourceAsset,
        limits: IngestionLimits,
    ) -> ExtractedDocument: ...


@runtime_checkable
class DatasetIngestor(Protocol):
    name: str
    version: str
    trust_level: IngestorTrustLevel

    async def prepare(
        self,
        request: DatasetIngestionRequest,
    ) -> FrozenIngestionSnapshot: ...


def normalized_records_fingerprint(
    records: Sequence[NormalizedCaseRecord],
) -> str:
    return fingerprint_json(
        [record.to_dict() for record in sorted(records, key=lambda item: item.case_id)]
    )


def rejected_records_fingerprint(records: Sequence[RejectedRecord]) -> str:
    return fingerprint_json(
        [
            record.to_dict()
            for record in sorted(
                records,
                key=lambda item: (
                    item.asset_id,
                    item.record_locator,
                    item.reason_code,
                ),
            )
        ]
    )


def _valid_field_selector(value: str) -> bool:
    if not isinstance(value, str) or not value or len(value) > 512:
        return False
    if value in {"$", ".", "record"}:
        return True
    forbidden = (
        "__",
        "$(",
        "${",
        "{{",
        "}}",
        "`",
        ";",
        "\n",
        "\r",
        "/",
        "\\",
        "[?",
    )
    if any(token in value for token in forbidden):
        return False
    return all(
        part == "*"
        or part.isdigit()
        or re.fullmatch(r"[A-Za-z_][A-Za-z0-9_-]*", part)
        for part in value.split(".")
    )


def _reject_forbidden_keys(value: Any, forbidden: set[str]) -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            normalized = str(key).lower().replace("-", "_")
            if normalized in forbidden or any(
                token in normalized
                for token in ("exec", "eval", "subprocess", "dynamic_import")
            ):
                raise IngestionContractError(
                    "generated_executable_not_allowed",
                    f"mapping field is forbidden: {key}",
                )
            _reject_forbidden_keys(item, forbidden)
    elif isinstance(value, (tuple, list)):
        for item in value:
            _reject_forbidden_keys(item, forbidden)
