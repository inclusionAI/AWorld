from __future__ import annotations

import fnmatch
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import yaml

from aworld.self_evolve.evaluation_plan import (
    SemanticIngestionProfileV1,
)
from .extractors import (
    builtin_extractors,
    extract_asset,
    read_regular_file,
)
from .types import (
    SOURCE_MANIFEST_SCHEMA_VERSION,
    AssetSelector,
    CaseFieldMappings,
    CaseSourceProvenance,
    DatasetExtractor,
    DatasetMappingSpec,
    DeclaredExclusion,
    FieldMapping,
    IngestionContractError,
    IngestionLimits,
    JoinSpec,
    NormalizedCaseRecord,
    RecordFramingSpec,
    RejectedRecord,
    SourceAsset,
    SourceInventory,
    TrajectoryMappingSpec,
    fingerprint_json,
    normalized_records_fingerprint,
    rejected_records_fingerprint,
    validate_fingerprint,
)


_SUCCESS_STATUSES = frozenset(
    {"success", "succeeded", "completed", "finished", "pass", "passed", "ok"}
)
_FAILURE_STATUSES = frozenset(
    {"cancelled", "error", "failed", "failure", "rejected", "timeout"}
)
_TERMINAL_STATUSES = _SUCCESS_STATUSES | _FAILURE_STATUSES
_MANIFEST_ALLOWED_KEYS = frozenset(
    {
        "schema_version",
        "assets",
        "case",
        "joins",
        "trajectory",
        "verification",
        "policy",
        "mapping",
        "semantics",
    }
)
_EXECUTABLE_KEY_TOKENS = (
    "code",
    "python",
    "shell",
    "subprocess",
    "exec",
    "eval",
    "template",
    "jinja",
    "regex",
    "dynamic_import",
    "module",
    "callable",
)


@dataclass(frozen=True)
class SourceManifestPolicy:
    allow_rejected_record_ratio: float = 0.0
    expected_output_required: bool = False
    trace_required: bool = False

    def __post_init__(self) -> None:
        if isinstance(self.allow_rejected_record_ratio, bool) or not isinstance(
            self.allow_rejected_record_ratio,
            (int, float),
        ):
            raise IngestionContractError(
                "manifest_invalid",
                "allow_rejected_record_ratio must be numeric",
            )
        ratio = float(self.allow_rejected_record_ratio)
        if ratio < 0.0 or ratio > 1.0:
            raise IngestionContractError(
                "manifest_invalid",
                "allow_rejected_record_ratio must be between 0 and 1",
            )
        object.__setattr__(self, "allow_rejected_record_ratio", ratio)

    def to_dict(self) -> dict[str, Any]:
        return {
            "allow_rejected_record_ratio": self.allow_rejected_record_ratio,
            "expected_output_required": self.expected_output_required,
            "trace_required": self.trace_required,
        }


@dataclass(frozen=True)
class SourceManifest:
    mapping_spec: DatasetMappingSpec | None = None
    semantic_profile: SemanticIngestionProfileV1 | None = None
    verification_command: str | None = None
    policy: SourceManifestPolicy = field(default_factory=SourceManifestPolicy)
    include_patterns: tuple[str, ...] = ()
    exclude_patterns: tuple[str, ...] = ()
    schema_version: str = SOURCE_MANIFEST_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != SOURCE_MANIFEST_SCHEMA_VERSION:
            raise IngestionContractError(
                "schema_version_mismatch",
                "invalid source manifest schema",
            )
        if self.mapping_spec is None and self.semantic_profile is None:
            raise IngestionContractError(
                "manifest_invalid",
                "source manifest requires mapping or semantics",
            )
        for pattern in (*self.include_patterns, *self.exclude_patterns):
            _validate_glob(pattern)
        if self.verification_command is not None:
            if (
                not isinstance(self.verification_command, str)
                or not self.verification_command.strip()
                or len(self.verification_command) > 4096
            ):
                raise IngestionContractError(
                    "manifest_invalid",
                    "verification command must be a bounded non-empty string",
                )

    @property
    def fingerprint(self) -> str:
        return fingerprint_json(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        result = {
            "schema_version": self.schema_version,
            "assets": {
                "include": list(self.include_patterns),
                "exclude": list(self.exclude_patterns),
            },
            "verification": (
                {"command": self.verification_command}
                if self.verification_command is not None
                else {}
            ),
            "policy": self.policy.to_dict(),
        }
        if self.mapping_spec is not None:
            result["mapping"] = self.mapping_spec.to_dict()
        if self.semantic_profile is not None:
            result["semantics"] = self.semantic_profile.to_dict()
        return result


@dataclass(frozen=True)
class MappingMaterialization:
    mapping_spec: DatasetMappingSpec
    normalized_cases: tuple[NormalizedCaseRecord, ...]
    rejected_records: tuple[RejectedRecord, ...]
    eligible_record_count: int
    selected_asset_ids: tuple[str, ...]
    required_asset_ids: tuple[str, ...]
    matched_required_asset_ids: tuple[str, ...]
    required_join_count: int = 0
    unmatched_required_join_count: int = 0
    join_cardinality_violation_count: int = 0
    source_escape_count: int = 0
    generated_executable_count: int = 0
    generated_command_count: int = 0
    held_out_value_exposure_count: int = 0

    @property
    def normalized_fingerprint(self) -> str:
        return normalized_records_fingerprint(self.normalized_cases)

    @property
    def rejected_fingerprint(self) -> str:
        return rejected_records_fingerprint(self.rejected_records)

    @property
    def materialization_fingerprint(self) -> str:
        return fingerprint_json(
            {
                "mapping_fingerprint": self.mapping_spec.fingerprint,
                "normalized_fingerprint": self.normalized_fingerprint,
                "rejected_fingerprint": self.rejected_fingerprint,
                "eligible_record_count": self.eligible_record_count,
                "selected_asset_ids": sorted(self.selected_asset_ids),
                "required_asset_ids": sorted(self.required_asset_ids),
                "matched_required_asset_ids": sorted(
                    self.matched_required_asset_ids
                ),
                "required_join_count": self.required_join_count,
                "unmatched_required_join_count": (
                    self.unmatched_required_join_count
                ),
                "join_cardinality_violation_count": (
                    self.join_cardinality_violation_count
                ),
            }
        )


@dataclass
class _WorkingRecord:
    values: dict[str, Any]
    assets: list[SourceAsset]
    locators: list[str]


def load_source_manifest(
    manifest_path: str | Path,
    *,
    source_root: str | Path,
) -> SourceManifest:
    root = Path(source_root).expanduser().absolute()
    supplied = Path(manifest_path).expanduser()
    path = supplied if supplied.is_absolute() else root / supplied
    if path.is_symlink():
        raise IngestionContractError(
            "source_symlink_not_allowed",
            "manifest cannot be a symlink",
        )
    try:
        resolved = path.resolve(strict=True)
        root_resolved = root.resolve(strict=True)
        resolved.relative_to(root_resolved)
    except (OSError, ValueError) as exc:
        raise IngestionContractError(
            "source_escape",
            "manifest must be a readable file inside the source root",
        ) from exc
    if not resolved.is_file():
        raise IngestionContractError(
            "manifest_invalid",
            "manifest path is not a regular file",
        )
    try:
        relative = resolved.relative_to(root_resolved).as_posix()
        payload = yaml.safe_load(
            read_regular_file(
                resolved,
                max_bytes=1024 * 1024,
                source_root=root_resolved,
                relative_path=relative,
            ).decode("utf-8")
        )
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
        raise IngestionContractError(
            "manifest_invalid",
            "manifest is not valid UTF-8 YAML",
        ) from exc
    return parse_source_manifest(payload)


def parse_source_manifest(payload: Any) -> SourceManifest:
    if not isinstance(payload, Mapping):
        raise IngestionContractError(
            "manifest_invalid",
            "source manifest must be an object",
        )
    if payload.get("schema_version") != SOURCE_MANIFEST_SCHEMA_VERSION:
        raise IngestionContractError(
            "schema_version_mismatch",
            f"expected schema_version={SOURCE_MANIFEST_SCHEMA_VERSION}",
        )
    unknown = set(str(key) for key in payload) - _MANIFEST_ALLOWED_KEYS
    if unknown:
        raise IngestionContractError(
            "manifest_invalid",
            f"unsupported manifest fields: {sorted(unknown)}",
        )
    _reject_executable_constructs(payload, allow_verification_command=True)
    assets = _as_mapping(payload.get("assets", {}), "assets")
    include = tuple(str(item) for item in assets.get("include", ()))
    exclude = tuple(str(item) for item in assets.get("exclude", ()))
    for pattern in (*include, *exclude):
        _validate_glob(pattern)

    has_legacy_mapping_sections = any(
        payload.get(key) is not None
        for key in ("case", "joins", "trajectory")
    )
    if payload.get("mapping") is not None:
        mapping_payload = _as_mapping(payload["mapping"], "mapping")
        mapping_spec = DatasetMappingSpec.from_dict(mapping_payload)
    elif has_legacy_mapping_sections:
        mapping_spec = _mapping_from_manifest_sections(payload, include, exclude)
    else:
        mapping_spec = None
    semantics_payload = payload.get("semantics")
    semantic_profile = (
        SemanticIngestionProfileV1.from_dict(
            _as_mapping(semantics_payload, "semantics")
        )
        if semantics_payload is not None
        else None
    )
    verification = _as_mapping(payload.get("verification", {}), "verification")
    unexpected_verification = set(verification) - {"command"}
    if unexpected_verification:
        raise IngestionContractError(
            "manifest_invalid",
            "verification only supports command",
        )
    policy_payload = _as_mapping(payload.get("policy", {}), "policy")
    policy_unknown = set(policy_payload) - {
        "allow_rejected_record_ratio",
        "expected_output_required",
        "trace_required",
    }
    if policy_unknown:
        raise IngestionContractError(
            "manifest_invalid",
            f"unsupported policy fields: {sorted(policy_unknown)}",
        )
    return SourceManifest(
        mapping_spec=mapping_spec,
        semantic_profile=semantic_profile,
        verification_command=verification.get("command"),
        policy=SourceManifestPolicy(
            allow_rejected_record_ratio=policy_payload.get(
                "allow_rejected_record_ratio",
                0.0,
            ),
            expected_output_required=bool(
                policy_payload.get("expected_output_required", False)
            ),
            trace_required=bool(policy_payload.get("trace_required", False)),
        ),
        include_patterns=include,
        exclude_patterns=exclude,
    )


def validate_mapping_spec(
    spec: DatasetMappingSpec | Mapping[str, Any],
) -> DatasetMappingSpec:
    if isinstance(spec, DatasetMappingSpec):
        # Round trip through the strict parser so newly added nested fields cannot
        # bypass the denylist by direct mapping construction.
        return DatasetMappingSpec.from_dict(spec.to_dict())
    return DatasetMappingSpec.from_dict(spec)


def materialize_mapping(
    source_path: str | Path,
    *,
    inventory: SourceInventory,
    mapping_spec: DatasetMappingSpec | Mapping[str, Any],
    manifest: SourceManifest | None = None,
    limits: IngestionLimits | None = None,
    extractors: Iterable[DatasetExtractor] | None = None,
) -> MappingMaterialization:
    spec = validate_mapping_spec(mapping_spec)
    if manifest is None and _uses_manifest_constant(spec):
        raise IngestionContractError(
            "mapping_protocol_invalid",
            "constant_from_manifest requires a user manifest",
        )
    active_limits = limits or IngestionLimits()
    source = Path(source_path).expanduser().absolute()
    root = source.parent if inventory.source_kind.value == "file" else source
    extractor_set = tuple(extractors or builtin_extractors())
    documents: dict[str, Any] = {}
    asset_by_id = {asset.asset_id: asset for asset in inventory.assets}
    for asset in inventory.assets:
        if asset.extractor_name is None:
            continue
        asset_path = _bounded_asset_path(root, asset.relative_path)
        read_regular_file(
            asset_path,
            max_bytes=active_limits.max_file_bytes,
            expected_fingerprint=asset.content_fingerprint,
            source_root=root,
            relative_path=asset.relative_path,
        )
        documents[asset.asset_id] = extract_asset(
            asset_path,
            asset=asset,
            limits=active_limits,
            extractors=extractor_set,
        )

    selectors = spec.asset_selectors or (
        AssetSelector(name="source", include=("**/*",), required=True),
    )
    selected: dict[str, tuple[SourceAsset, ...]] = {}
    required_ids: set[str] = set()
    matched_required_ids: set[str] = set()
    for selector in selectors:
        matches = tuple(
            asset
            for asset in inventory.assets
            if asset.asset_id in documents and _asset_matches(asset, selector)
        )
        selected[selector.name] = matches
        if selector.required:
            all_required = {
                asset.asset_id
                for asset in inventory.assets
                if _asset_matches(asset, selector, require_supported=False)
            }
            required_ids.update(all_required)
            matched_required_ids.update(asset.asset_id for asset in matches)
    selected_ids = {
        asset.asset_id for matches in selected.values() for asset in matches
    }

    primary_name = spec.record_framing.asset_selector or selectors[0].name
    working, extraction_rejections = _frame_records(
        selected.get(primary_name, ()),
        documents,
        primary_name,
        spec.record_framing,
    )
    rejected: list[RejectedRecord] = list(extraction_rejections)
    eligible = len(working) + len(extraction_rejections)
    unmatched_required = 0
    cardinality_violations = 0
    for join in spec.joins:
        right_records, right_rejections = _frame_records(
            selected.get(join.right_asset, ()),
            documents,
            join.right_asset,
            RecordFramingSpec(
                kind=spec.record_framing.kind,
                asset_selector=join.right_asset,
                delimiter=spec.record_framing.delimiter,
            ),
        )
        rejected.extend(right_rejections)
        working, join_rejections, unmatched, violations = _join_records(
            working,
            right_records,
            join,
        )
        rejected.extend(join_rejections)
        unmatched_required += unmatched
        cardinality_violations += violations

    if len(working) > active_limits.max_cases:
        raise IngestionContractError(
            "source_limit_exceeded",
            "normalized case count would exceed max_cases",
        )

    normalized: list[NormalizedCaseRecord] = []
    for item in working:
        try:
            normalized.append(
                _normalize_record(
                    item,
                    spec=spec,
                    manifest=manifest,
                )
            )
        except IngestionContractError as exc:
            rejected.append(
                RejectedRecord(
                    reason_code=exc.reason_code,
                    asset_id=item.assets[0].asset_id,
                    record_locator=item.locators[0],
                )
            )
    return MappingMaterialization(
        mapping_spec=spec,
        normalized_cases=tuple(normalized),
        rejected_records=tuple(rejected),
        eligible_record_count=len(normalized) + len(rejected),
        selected_asset_ids=tuple(sorted(selected_ids)),
        required_asset_ids=tuple(sorted(required_ids)),
        matched_required_asset_ids=tuple(sorted(matched_required_ids)),
        required_join_count=sum(int(join.required) for join in spec.joins),
        unmatched_required_join_count=unmatched_required,
        join_cardinality_violation_count=cardinality_violations,
    )


def _mapping_from_manifest_sections(
    payload: Mapping[str, Any],
    include: tuple[str, ...],
    exclude: tuple[str, ...],
) -> DatasetMappingSpec:
    case = _as_mapping(payload.get("case", {}), "case")
    if not case:
        raise IngestionContractError(
            "manifest_invalid",
            "manifest must define mapping or case",
        )
    framing_value = case.get("framing", "one_file_per_case")
    if framing_value == "one_request_per_file":
        framing_value = "one_file_per_case"
    if isinstance(framing_value, Mapping):
        framing = RecordFramingSpec.from_dict(framing_value)
    else:
        framing = RecordFramingSpec(kind=str(framing_value))
    fields_payload = {
        key: value
        for key, value in case.items()
        if key in {"id", "case_id", "input", "expected_output", "metadata"}
    }
    if "id" in fields_payload and "case_id" not in fields_payload:
        fields_payload["case_id"] = fields_payload.pop("id")
    fields_payload.pop("framing", None)
    fields = CaseFieldMappings.from_dict(fields_payload)
    selector_names: list[str] = []
    selector_patterns: dict[str, list[str]] = {}
    for pattern in include or ("**/*",):
        prefix = pattern.split("/", 1)[0]
        name = (
            prefix
            if prefix not in {"*", "**"} and prefix.replace("_", "").replace("-", "").isalnum()
            else "source"
        )
        selector_patterns.setdefault(name, []).append(pattern)
        if name not in selector_names:
            selector_names.append(name)
    selectors = tuple(
        AssetSelector(
            name=name,
            include=tuple(selector_patterns[name]),
            exclude=exclude,
        )
        for name in selector_names
    )
    field_sources = [
        mapping.source
        for mapping in (
            fields.case_id,
            fields.input,
            fields.expected_output,
            fields.metadata,
        )
        if mapping is not None and mapping.source
    ]
    primary_selector = next(
        (
            source.split(".", 1)[0]
            for source in field_sources
            if "." in source and source.split(".", 1)[0] in selector_names
        ),
        selector_names[0],
    )
    framing = RecordFramingSpec(
        kind=framing.kind,
        asset_selector=primary_selector,
        delimiter=framing.delimiter,
    )
    joins = tuple(
        JoinSpec.from_dict(_as_mapping(item, "join"))
        for item in payload.get("joins", ())
    )
    trajectory_payload = payload.get("trajectory")
    trajectory = (
        TrajectoryMappingSpec.from_dict(
            _as_mapping(trajectory_payload, "trajectory")
        )
        if trajectory_payload is not None
        else None
    )
    return DatasetMappingSpec(
        mapping_id="manifest-mapping",
        asset_selectors=selectors,
        record_framing=framing,
        fields=fields,
        joins=joins,
        trajectory=trajectory,
        rationale_codes=("user_manifest",),
    )


def _frame_records(
    assets: Sequence[SourceAsset],
    documents: Mapping[str, Any],
    selector_name: str,
    framing: RecordFramingSpec,
) -> tuple[list[_WorkingRecord], list[RejectedRecord]]:
    output: list[_WorkingRecord] = []
    rejected: list[RejectedRecord] = []
    for asset in sorted(assets, key=lambda item: item.relative_path):
        document = documents[asset.asset_id]
        invalid_locators = tuple(
            str(item)
            for item in document.structural_profile.get(
                "invalid_record_locators",
                (),
            )
        )
        rejected.extend(
            RejectedRecord(
                reason_code="record_parse_failed",
                asset_id=asset.asset_id,
                record_locator=locator,
            )
            for locator in invalid_locators
        )
        framed: list[tuple[str, Any]]
        if framing.kind == "one_file_per_case":
            if len(document.records) == 1:
                value = document.records[0].value
            else:
                value = [record.value for record in document.records]
            framed = [("$", value)]
        elif framing.kind == "literal_delimited_blocks":
            if len(document.records) != 1 or not isinstance(
                document.records[0].value,
                str,
            ):
                rejected.append(
                    RejectedRecord(
                        reason_code="record_framing_invalid",
                        asset_id=asset.asset_id,
                        record_locator="$",
                    )
                )
                continue
            blocks = document.records[0].value.split(framing.delimiter or "")
            framed = [
                (f"block:{index}", block)
                for index, block in enumerate(blocks, start=1)
                if block.strip()
            ]
        else:
            framed = [
                (record.locator, record.value) for record in document.records
            ]
        output.extend(
            _WorkingRecord(
                values={selector_name: value, "record": value},
                assets=[asset],
                locators=[locator],
            )
            for locator, value in framed
        )
    return output, rejected


def _join_records(
    left_records: list[_WorkingRecord],
    right_records: list[_WorkingRecord],
    join: JoinSpec,
) -> tuple[list[_WorkingRecord], list[RejectedRecord], int, int]:
    right_index: dict[str, list[_WorkingRecord]] = {}
    for right in right_records:
        key = _selector_value(right.values.get(join.right_asset), join.right_key)
        if key is None:
            continue
        right_index.setdefault(_join_key(key), []).append(right)
    violations = 0
    if join.cardinality in {"one_to_one", "many_to_one"}:
        violations += sum(
            1 for matches in right_index.values() if len(matches) > 1
        )
    left_key_counts: dict[str, int] = {}
    for left in left_records:
        key = _selector_value(left.values.get(join.left_asset), join.left_key)
        if key is not None:
            normalized = _join_key(key)
            left_key_counts[normalized] = left_key_counts.get(normalized, 0) + 1
    if join.cardinality in {"one_to_one", "one_to_many"}:
        violations += sum(1 for count in left_key_counts.values() if count > 1)

    output: list[_WorkingRecord] = []
    rejected: list[RejectedRecord] = []
    unmatched = 0
    for left in left_records:
        key = _selector_value(left.values.get(join.left_asset), join.left_key)
        matches = right_index.get(_join_key(key), ()) if key is not None else ()
        if not matches:
            if join.required:
                unmatched += 1
            if join.join_type == "left" and join.unmatched_policy == "allow":
                output.append(left)
            else:
                rejected.append(
                    RejectedRecord(
                        reason_code=(
                            "required_join_unmatched"
                            if join.required
                            else "join_unmatched"
                        ),
                        asset_id=left.assets[0].asset_id,
                        record_locator=left.locators[0],
                    )
                )
            continue
        for right in matches:
            merged = dict(left.values)
            merged[join.right_asset] = right.values.get(join.right_asset)
            output.append(
                _WorkingRecord(
                    values=merged,
                    assets=[*left.assets, *right.assets],
                    locators=[*left.locators, *right.locators],
                )
            )
    return output, rejected, unmatched, violations


def _normalize_record(
    item: _WorkingRecord,
    *,
    spec: DatasetMappingSpec,
    manifest: SourceManifest | None,
) -> NormalizedCaseRecord:
    input_value = _apply_mapping(spec.fields.input, item.values)
    if input_value is None:
        raise IngestionContractError("input_missing", "case input is null")
    expected = (
        _apply_mapping(spec.fields.expected_output, item.values)
        if spec.fields.expected_output is not None
        else None
    )
    metadata = (
        _apply_mapping(spec.fields.metadata, item.values)
        if spec.fields.metadata is not None
        else {}
    )
    if metadata is None:
        metadata = {}
    if not isinstance(metadata, Mapping):
        raise IngestionContractError(
            "metadata_invalid",
            "metadata mapping must produce an object",
        )
    case_id_value = (
        _apply_mapping(spec.fields.case_id, item.values)
        if spec.fields.case_id is not None
        else None
    )
    if case_id_value is None or str(case_id_value).strip() == "":
        identity = fingerprint_json(
            {
                "asset_ids": [asset.asset_id for asset in item.assets],
                "record_locators": item.locators,
            }
        )
        case_id = "case-" + identity.removeprefix("sha256:")[:24]
    else:
        case_id = str(case_id_value)

    trajectory = None
    replayability = "absent"
    if spec.trajectory is not None:
        trajectory, replayability = _normalize_trajectory(
            item.values,
            spec.trajectory,
            fallback_task_id=case_id,
        )
    verification_command = (
        manifest.verification_command if manifest is not None else None
    )
    source = CaseSourceProvenance(
        asset_ids=tuple(asset.asset_id for asset in item.assets),
        record_locators=tuple(item.locators),
        mapping_fingerprint=spec.fingerprint,
        verification_origin=(
            "user_manifest" if verification_command is not None else None
        ),
    )
    return NormalizedCaseRecord(
        case_id=case_id,
        input=input_value,
        expected_output=expected,
        verification_command=verification_command,
        metadata=dict(metadata),
        trajectory=trajectory,
        trace_replayability=replayability,
        source=source,
    )


def _normalize_trajectory(
    context: Mapping[str, Any],
    spec: TrajectoryMappingSpec,
    *,
    fallback_task_id: str,
) -> tuple[Mapping[str, Any], str]:
    raw_steps = _apply_mapping(spec.steps, context)
    if isinstance(raw_steps, str):
        try:
            raw_steps = json.loads(raw_steps)
        except json.JSONDecodeError as exc:
            raise IngestionContractError(
                "trajectory_invalid",
                "trajectory steps are not valid JSON",
            ) from exc
    if not isinstance(raw_steps, list):
        raise IngestionContractError(
            "trajectory_invalid",
            "trajectory steps must be an array",
        )
    task_id = (
        _apply_mapping(spec.task_id, context)
        if spec.task_id is not None
        else fallback_task_id
    )
    normalized_steps: list[dict[str, Any]] = []
    for index, raw_step in enumerate(raw_steps):
        step_context = {
            "step": raw_step,
            **(dict(raw_step) if isinstance(raw_step, Mapping) else {}),
        }
        step_id = _step_field(
            spec.step_fields.id,
            step_context,
            default=(
                raw_step.get("id")
                if isinstance(raw_step, Mapping)
                else f"step-{index + 1}"
            ),
        )
        meta = _step_field(
            spec.step_fields.meta,
            step_context,
            default=(
                raw_step.get("meta", {})
                if isinstance(raw_step, Mapping)
                else {}
            ),
        )
        state = _step_field(
            spec.step_fields.state,
            step_context,
            default=(
                raw_step.get("state", {})
                if isinstance(raw_step, Mapping)
                else {}
            ),
        )
        action = _step_field(
            spec.step_fields.action,
            step_context,
            default=(
                raw_step.get("action", {})
                if isinstance(raw_step, Mapping)
                else {}
            ),
        )
        reward = _step_field(
            spec.step_fields.reward,
            step_context,
            default=(
                raw_step.get("reward", {})
                if isinstance(raw_step, Mapping)
                else {}
            ),
        )
        if not isinstance(meta, Mapping):
            meta = {"value": meta}
        if not isinstance(state, Mapping):
            state = {"value": state}
        if not isinstance(action, Mapping):
            action = {"content": action}
        if not isinstance(reward, Mapping):
            reward = {"status": reward}
        reward = dict(reward)
        status = reward.get("status")
        reward["status"] = normalize_trajectory_status(
            status,
            status_map=spec.status_map,
        )
        if reward["status"] == "unknown" and status is not None:
            reward["original_status_fingerprint"] = fingerprint_json(
                str(status).strip().lower()
            )
        normalized_steps.append(
            {
                "id": str(step_id or f"step-{index + 1}"),
                "meta": dict(meta),
                "state": dict(state),
                "action": dict(action),
                "reward": reward,
            }
        )
    replayability = _trajectory_replayability(normalized_steps)
    return {"task_id": str(task_id), "steps": normalized_steps}, replayability


def normalize_trajectory_status(
    value: Any,
    *,
    status_map: Mapping[str, str] | None = None,
) -> str:
    if value is None:
        return "unknown"
    normalized = str(value).strip().lower()
    mapped = (status_map or {}).get(normalized, normalized)
    return mapped if mapped in _TERMINAL_STATUSES else "unknown"


def _trajectory_replayability(steps: Sequence[Mapping[str, Any]]) -> str:
    if not steps:
        return "incomplete"
    first_state = steps[0].get("state")
    has_input = isinstance(first_state, Mapping) and first_state.get("input") is not None
    has_action = any(
        isinstance(step.get("action"), Mapping)
        and (
            step["action"].get("content") is not None
            and step["action"].get("content") != ""
            or bool(step["action"].get("tool_calls"))
        )
        for step in steps
    )
    terminal = steps[-1].get("reward")
    has_terminal = (
        isinstance(terminal, Mapping)
        and terminal.get("status") in _TERMINAL_STATUSES
    )
    return "replayable" if has_input and has_action and has_terminal else "incomplete"


def _step_field(
    mapping: FieldMapping | None,
    context: Mapping[str, Any],
    *,
    default: Any,
) -> Any:
    return _apply_mapping(mapping, context) if mapping is not None else default


def _apply_mapping(
    mapping: FieldMapping | None,
    context: Mapping[str, Any],
) -> Any:
    if mapping is None:
        return None
    selectors = tuple(
        selector
        for selector in (mapping.source, *mapping.sources)
        if selector is not None
    )
    values = [_selector_value(context, selector) for selector in selectors]
    if mapping.transform == "constant_from_manifest":
        return mapping.constant
    if mapping.transform == "coalesce":
        return next((value for value in values if value is not None), None)
    value = values[0] if values else mapping.constant
    if mapping.transform == "identity":
        return value
    if mapping.transform == "stringify":
        if isinstance(value, str):
            return value
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    if mapping.transform == "parse_json":
        if not isinstance(value, str):
            raise IngestionContractError(
                "mapping_execution_failed",
                "parse_json requires a string",
            )
        try:
            return json.loads(value)
        except json.JSONDecodeError as exc:
            raise IngestionContractError(
                "mapping_execution_failed",
                "parse_json input is invalid",
            ) from exc
    if mapping.transform == "bounded_join":
        if len(values) > 64:
            raise IngestionContractError(
                "mapping_execution_failed",
                "bounded_join accepts at most 64 values",
            )
        return "".join(str(value) for value in values if value is not None)[:65536]
    if mapping.transform == "status_map":
        return normalize_trajectory_status(value)
    raise IngestionContractError(
        "mapping_capability_not_supported",
        f"unsupported transform {mapping.transform}",
    )


def _selector_value(value: Any, selector: str) -> Any:
    if selector in {"$", ".", "record"}:
        if selector == "record" and isinstance(value, Mapping) and "record" in value:
            return value["record"]
        return value
    current = value
    for part in selector.split("."):
        if part == "*":
            if not isinstance(current, list):
                return None
            return current
        if isinstance(current, Mapping):
            if part not in current:
                return None
            current = current[part]
        elif isinstance(current, list) and part.isdigit():
            index = int(part)
            if index >= len(current):
                return None
            current = current[index]
        else:
            return None
    return current


def _join_key(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _asset_matches(
    asset: SourceAsset,
    selector: AssetSelector,
    *,
    require_supported: bool = True,
) -> bool:
    if require_supported and asset.extractor_name is None:
        return False
    if selector.media_types and asset.media_type not in selector.media_types:
        return False
    if not any(_glob_match(asset.relative_path, pattern) for pattern in selector.include):
        return False
    return not any(
        _glob_match(asset.relative_path, pattern) for pattern in selector.exclude
    )


def _glob_match(relative_path: str, pattern: str) -> bool:
    if pattern in {"*", "**", "**/*"}:
        return True
    if fnmatch.fnmatchcase(relative_path, pattern):
        return True
    if pattern.startswith("**/") and fnmatch.fnmatchcase(
        relative_path,
        pattern[3:],
    ):
        return True
    return False


def _bounded_asset_path(root: Path, relative_path: str) -> Path:
    try:
        root_resolved = root.resolve(strict=True)
        path = root / relative_path
        if path.is_symlink():
            raise ValueError("symlink")
        resolved = path.resolve(strict=True)
        resolved.relative_to(root_resolved)
    except (OSError, ValueError) as exc:
        raise IngestionContractError(
            "source_escape",
            "asset path escaped the source root",
        ) from exc
    return resolved


def _validate_glob(pattern: str) -> None:
    if (
        not isinstance(pattern, str)
        or not pattern
        or Path(pattern).is_absolute()
        or ".." in Path(pattern).parts
        or "\x00" in pattern
    ):
        raise IngestionContractError(
            "source_escape",
            "manifest glob must remain inside source root",
        )


def _reject_executable_constructs(
    value: Any,
    *,
    allow_verification_command: bool,
    path: tuple[str, ...] = (),
) -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            normalized = str(key).lower().replace("-", "_")
            current = (*path, normalized)
            if (
                normalized == "command"
                and allow_verification_command
                and current == ("verification", "command")
            ):
                continue
            key_tokens = frozenset(normalized.split("_"))
            if normalized == "command" or any(
                token == normalized or token in key_tokens
                for token in _EXECUTABLE_KEY_TOKENS
            ):
                raise IngestionContractError(
                    "generated_executable_not_allowed",
                    f"executable manifest construct is forbidden: {'.'.join(current)}",
                )
            _reject_executable_constructs(
                item,
                allow_verification_command=allow_verification_command,
                path=current,
            )
    elif isinstance(value, (tuple, list)):
        for item in value:
            _reject_executable_constructs(
                item,
                allow_verification_command=allow_verification_command,
                path=path,
            )


def _as_mapping(value: Any, field_name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise IngestionContractError(
            "manifest_invalid",
            f"{field_name} must be an object",
        )
    return value


def _uses_manifest_constant(spec: DatasetMappingSpec) -> bool:
    mappings: list[FieldMapping | None] = [
        spec.fields.case_id,
        spec.fields.input,
        spec.fields.expected_output,
        spec.fields.metadata,
    ]
    if spec.trajectory is not None:
        mappings.extend(
            [
                spec.trajectory.task_id,
                spec.trajectory.steps,
                spec.trajectory.step_fields.id,
                spec.trajectory.step_fields.meta,
                spec.trajectory.step_fields.state,
                spec.trajectory.step_fields.action,
                spec.trajectory.step_fields.reward,
            ]
        )
    return any(
        mapping is not None and mapping.transform == "constant_from_manifest"
        for mapping in mappings
    )
