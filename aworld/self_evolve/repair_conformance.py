from __future__ import annotations

import ast
import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Any, Mapping, Sequence

from aworld.self_evolve.replay_capability import (
    REPLAY_CAPABILITY_SCHEMA_VERSION,
    ReplayServiceSpec,
)
from aworld.self_evolve.sanitization import sanitize_path_ref, sanitize_text
from aworld.self_evolve.types import CandidateVariant


_SOURCE_SUFFIXES = frozenset(
    {".c", ".cc", ".go", ".java", ".js", ".jsx", ".py", ".rb", ".rs", ".sh", ".ts", ".tsx"}
)
_MAX_CONTRACT_FILES = 16
_MAX_OBSERVED_OPERATIONS = 8


@dataclass(frozen=True)
class ExactRepairProbe:
    kind: str
    path: str
    expected_response: str


@dataclass(frozen=True)
class RepairConformanceContract:
    focus_candidate_id: str
    failure_codes: tuple[str, ...]
    interaction_progress: int
    base_file_fingerprints: Mapping[str, str]
    required_branch_paths: tuple[str, ...]
    base_branch_fingerprints: Mapping[str, str]
    base_fixture_selector_fingerprints: Mapping[str, str] = field(
        default_factory=dict
    )
    manifest_path: str | None = None
    exact_probe: ExactRepairProbe | None = None
    late_observed_operations: tuple[str, ...] = ()
    requires_fixture_derived_probe: bool = False
    required_fixture_probe_operations: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "focus_candidate_id": self.focus_candidate_id,
            "failure_codes": list(self.failure_codes),
            "interaction_progress": self.interaction_progress,
            "base_file_fingerprints": dict(self.base_file_fingerprints),
            "required_branch_paths": list(self.required_branch_paths),
            "base_branch_fingerprints": dict(self.base_branch_fingerprints),
            "base_fixture_selector_fingerprints": dict(
                self.base_fixture_selector_fingerprints
            ),
            "manifest_path": self.manifest_path,
            "exact_probe": (
                {
                    "kind": self.exact_probe.kind,
                    "path": self.exact_probe.path,
                    "expected_response": self.exact_probe.expected_response,
                }
                if self.exact_probe is not None
                else None
            ),
            "late_observed_operations": list(self.late_observed_operations),
            "requires_fixture_derived_probe": self.requires_fixture_derived_probe,
            "required_fixture_probe_operations": list(
                self.required_fixture_probe_operations
            ),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "RepairConformanceContract":
        exact_raw = value.get("exact_probe")
        exact_probe = (
            ExactRepairProbe(
                kind=str(exact_raw.get("kind") or ""),
                path=str(exact_raw.get("path") or "/"),
                expected_response=str(exact_raw.get("expected_response") or ""),
            )
            if isinstance(exact_raw, Mapping)
            else None
        )
        raw_fingerprints = value.get("base_file_fingerprints")
        fingerprints = (
            {
                str(path): str(fingerprint)
                for path, fingerprint in raw_fingerprints.items()
                if isinstance(path, str) and isinstance(fingerprint, str)
            }
            if isinstance(raw_fingerprints, Mapping)
            else {}
        )
        raw_branch_fingerprints = value.get("base_branch_fingerprints")
        branch_fingerprints = (
            {
                str(key): str(fingerprint)
                for key, fingerprint in raw_branch_fingerprints.items()
                if isinstance(key, str) and isinstance(fingerprint, str)
            }
            if isinstance(raw_branch_fingerprints, Mapping)
            else {}
        )
        raw_selector_fingerprints = value.get(
            "base_fixture_selector_fingerprints"
        )
        selector_fingerprints = (
            {
                str(key): str(fingerprint)
                for key, fingerprint in raw_selector_fingerprints.items()
                if isinstance(key, str) and isinstance(fingerprint, str)
            }
            if isinstance(raw_selector_fingerprints, Mapping)
            else {}
        )
        return cls(
            focus_candidate_id=str(value.get("focus_candidate_id") or ""),
            failure_codes=_string_tuple(value.get("failure_codes")),
            interaction_progress=_non_negative_int(value.get("interaction_progress")),
            base_file_fingerprints=fingerprints,
            required_branch_paths=_string_tuple(value.get("required_branch_paths")),
            base_branch_fingerprints=branch_fingerprints,
            base_fixture_selector_fingerprints=selector_fingerprints,
            manifest_path=(
                str(value.get("manifest_path"))
                if isinstance(value.get("manifest_path"), str)
                else None
            ),
            exact_probe=exact_probe,
            late_observed_operations=_string_tuple(
                value.get("late_observed_operations")
            ),
            requires_fixture_derived_probe=(
                value.get("requires_fixture_derived_probe") is True
            ),
            required_fixture_probe_operations=_string_tuple(
                value.get("required_fixture_probe_operations")
            ),
        )


@dataclass(frozen=True)
class RepairConformanceResult:
    passed: bool
    code: str
    reason: str
    details: Mapping[str, object]

    def to_dict(self) -> dict[str, object]:
        return {
            "passed": self.passed,
            "code": self.code,
            "reason": self.reason,
            "details": dict(self.details),
        }


def compile_repair_conformance_contract(
    repair_focus: Mapping[str, object] | None,
) -> RepairConformanceContract | None:
    if not isinstance(repair_focus, Mapping):
        return None
    package = repair_focus.get("repair_candidate_package")
    if not isinstance(package, Mapping):
        return None
    focus_candidate_id = package.get("candidate_id")
    raw_files = package.get("files")
    if (
        not isinstance(focus_candidate_id, str)
        or not focus_candidate_id
        or not isinstance(raw_files, list)
        or not raw_files
    ):
        return None

    base_sources: dict[str, str] = {}
    for item in raw_files[:_MAX_CONTRACT_FILES]:
        if not isinstance(item, Mapping):
            continue
        path = _bounded_relative_path(item.get("path"))
        content = item.get("content")
        if path is None or not isinstance(content, str):
            continue
        base_sources[path] = content
    if not base_sources:
        return None

    diagnostics = tuple(_diagnostic_mappings(repair_focus))
    direct_failure_codes = _diagnostic_failure_codes(diagnostics)
    inherited_contract = _inherited_repair_conformance_contract(diagnostics)
    failure_codes = tuple(
        dict.fromkeys(
            (
                *direct_failure_codes,
                *(
                    inherited_contract.failure_codes
                    if inherited_contract is not None
                    else ()
                ),
            )
        )
    )
    manifest_path, branch_paths = _replay_implementation_paths(base_sources)
    exact_probe = _exact_probe_constraint(diagnostics) or (
        inherited_contract.exact_probe
        if inherited_contract is not None
        else None
    )
    directly_observed_operations = _observed_operations(diagnostics)
    observed_operations = directly_observed_operations or (
        inherited_contract.late_observed_operations
        if inherited_contract is not None
        else ()
    )
    requires_fixture_probe = (
        "implement_observed_endpoint_interactions" in failure_codes
        or bool(
            inherited_contract is not None
            and inherited_contract.requires_fixture_derived_probe
        )
    )
    inherited_verified_operations = (
        inherited_contract.required_fixture_probe_operations
        if inherited_contract is not None
        else ()
    )
    frontier_operations = tuple(
        operation
        for operation in directly_observed_operations
        if operation not in inherited_verified_operations
    )
    if requires_fixture_probe and frontier_operations:
        required_fixture_probe_operations = tuple(
            dict.fromkeys(
                (*inherited_verified_operations, frontier_operations[-1])
            )
        )[-_MAX_OBSERVED_OPERATIONS:]
    elif (
        requires_fixture_probe
        and inherited_contract is not None
        and inherited_contract.required_fixture_probe_operations
    ):
        required_fixture_probe_operations = (
            inherited_contract.required_fixture_probe_operations
        )
    elif requires_fixture_probe:
        required_fixture_probe_operations = observed_operations[-1:]
    else:
        required_fixture_probe_operations = ()
    interaction_progress = max(
        _non_negative_int(repair_focus.get("interaction_progress")),
        (
            inherited_contract.interaction_progress
            if inherited_contract is not None
            else 0
        ),
    )
    return RepairConformanceContract(
        focus_candidate_id=sanitize_text(focus_candidate_id, max_chars=160),
        failure_codes=failure_codes,
        interaction_progress=interaction_progress,
        base_file_fingerprints={
            path: _source_fingerprint(content)
            for path, content in sorted(base_sources.items())
        },
        required_branch_paths=branch_paths,
        base_branch_fingerprints=_base_branch_fingerprints(
            base_sources,
            branch_paths=branch_paths,
            markers=observed_operations,
        ),
        base_fixture_selector_fingerprints=(
            _fixture_selector_fingerprints(
                base_sources,
                branch_paths=branch_paths,
                markers=observed_operations,
            )
            if requires_fixture_probe
            else {}
        ),
        manifest_path=manifest_path,
        exact_probe=exact_probe,
        late_observed_operations=observed_operations,
        requires_fixture_derived_probe=requires_fixture_probe,
        required_fixture_probe_operations=required_fixture_probe_operations,
    )


def evaluate_candidate_source_conformance(
    candidate: CandidateVariant,
    contract: RepairConformanceContract,
) -> RepairConformanceResult:
    # ``CandidateVariant.files`` is a delta, not a materialized package.  A
    # missing runtime path therefore means that the candidate inherited the
    # baseline implementation.  Treating a missing path as an empty source
    # would fingerprint it as changed and let a rationale-only (or
    # compiler-only) candidate through the static gate.
    candidate_sources = {
        item.path: item.content
        for item in candidate.files
        if item.operation == "upsert" and isinstance(item.content, str)
    }
    removed_branch_paths = sorted(
        path
        for path in contract.required_branch_paths
        if any(
            item.path == path and item.operation == "delete"
            for item in candidate.files
        )
    )
    if removed_branch_paths:
        return RepairConformanceResult(
            passed=False,
            code="repair_branch_removed",
            reason=(
                "candidate deletes the focused replay implementation instead of "
                "providing a replacement implementation"
            ),
            details={
                "focus_candidate_id": contract.focus_candidate_id,
                "removed_paths": removed_branch_paths,
            },
        )
    changed_file_paths = [
        path
        for path in contract.required_branch_paths
        if path in candidate_sources
        and _source_fingerprint(candidate_sources[path])
        != contract.base_file_fingerprints.get(path)
    ]
    changed_branch_slices = _changed_branch_slices(
        candidate_sources,
        contract.base_branch_fingerprints,
    )
    changed_selector_slices = _changed_fixture_selector_slices(
        candidate_sources,
        contract.base_fixture_selector_fingerprints,
    )
    if changed_branch_slices or changed_selector_slices or (
        not contract.base_branch_fingerprints and changed_file_paths
    ):
        structural_failure = _fixture_probe_structure_failure(
            candidate_sources,
            contract=contract,
        )
        if structural_failure is not None:
            return structural_failure
        violations = _fixture_probe_derivation_violations(
            candidate_sources,
            required=contract.requires_fixture_derived_probe,
        )
        if violations:
            required_change = (
                "load AWORLD_REPLAY_RESPONSE_INDEX, select a non_empty record "
                "for the incoming operation, and return its decoded value or "
                "protocol projection; remove any helper that returns "
                "FIXTURE_DATA or FIXTURE_DATA.get(key, [])"
                if any(
                    item.get("construct") == "top_level_fixture_projection"
                    for item in violations
                )
                else (
                    "select any deterministic non-empty recorded response leaf "
                    "after a complete recursive gateway-discovery phase and a "
                    "separate payload traversal phase"
                )
            )
            return RepairConformanceResult(
                passed=False,
                code="forbidden_fixture_probe_derivation",
                reason=(
                    "the changed fixture-probe branch still filters recorded "
                    "scalars by shape, derives an assertion from a hash, or skips "
                    "nested sequence roots or payload selection during response-"
                    "gateway reconstruction"
                ),
                details={
                    "focus_candidate_id": contract.focus_candidate_id,
                    "violations": violations,
                    "required_change": required_change,
                    "forbidden_derivations": [
                        "regex scalar filters",
                        "narrow scalar length filters",
                        "fixture hash assertion fallbacks",
                        "returning a non-mapping composite before traversing sequences",
                        "passing a gateway directly to a scalar selector before entering a payload key",
                        "falling through from a non-empty gateway branch into a parsed-root scalar fallback",
                    ],
                },
            )
        operation_failure = _operation_response_correlation_failure(
            candidate_sources,
            contract=contract,
        )
        if operation_failure is not None:
            return operation_failure
        return _passed(
            "repair_branch_changed",
            "candidate materially changes the focused replay implementation",
            changed_paths=(
                changed_branch_slices
                or changed_selector_slices
                or changed_file_paths
            ),
        )

    redirected_paths: tuple[str, ...] = ()
    if contract.manifest_path is not None:
        manifest_content = candidate_sources.get(contract.manifest_path)
        if (
            isinstance(manifest_content, str)
            and _source_fingerprint(manifest_content)
            != contract.base_file_fingerprints.get(contract.manifest_path)
        ):
            _, redirected_paths = _replay_implementation_paths(
                {contract.manifest_path: manifest_content, **candidate_sources}
            )
            if (
                redirected_paths
                and redirected_paths != contract.required_branch_paths
                and all(
                    isinstance(candidate_sources.get(path), str)
                    and bool(_canonical_source(candidate_sources[path]))
                    for path in redirected_paths
                )
            ):
                return _passed(
                    "repair_branch_redirected",
                    "candidate redirects the replay manifest to a new implementation",
                    changed_paths=[contract.manifest_path, *redirected_paths],
                )

    return RepairConformanceResult(
        passed=False,
        code="repair_branch_unchanged",
        reason=(
            "candidate rationale is not evidence: the focused replay implementation "
            "source is unchanged"
        ),
        details={
            "focus_candidate_id": contract.focus_candidate_id,
            "required_changed_paths": list(contract.required_branch_paths),
            "observed_request_operations": list(
                contract.late_observed_operations
            ),
            "observed_candidate_paths": sorted(candidate_sources)[:32],
        },
    )


def _operation_response_correlation_failure(
    sources: Mapping[str, str],
    *,
    contract: RepairConformanceContract,
) -> RepairConformanceResult | None:
    """Reject a task-plane branch that ignores the request it is repairing.

    A declaration-only candidate can pass a readiness probe while returning one
    fixture container for every operation.  The generic conformance contract has
    no protocol-specific schema, but it can still require the changed handler to
    consume either the observed operation's request parameters or a deterministic
    response map/cursor.  This catches the common failure before an expensive
    task rollout without imposing a browser/CDP implementation on the framework.
    """

    operations = tuple(
        operation
        for operation in (
            contract.required_fixture_probe_operations
            or contract.late_observed_operations[-1:]
        )
        if operation
    )
    # A shallow synthetic feedback package may only prove that a selector
    # changed.  Require operation correlation once the replay has actually
    # reached the task/data plane (the interaction progress counter is emitted
    # by the runner from the protocol trace), preserving selector-focused
    # conformance checks for transport-only repairs.
    if (
        not contract.requires_fixture_derived_probe
        or not operations
        or contract.interaction_progress < 4
    ):
        return None
    source_text = "\n".join(sources.values())
    has_index_binding = "AWORLD_REPLAY_RESPONSE_INDEX" in source_text
    if not has_index_binding:
        return RepairConformanceResult(
            passed=False,
            code="operation_response_uncorrelated",
            reason=(
                "task-plane fixture response does not consume the framework's "
                "immutable operation-response index"
            ),
            details={
                "operation": next(iter(operations), "unknown"),
                "required_change": (
                    "load AWORLD_REPLAY_RESPONSE_INDEX (and, when needed, "
                    "AWORLD_REPLAY_FIXTURE_PATH), select a non_empty record for "
                    "the incoming operation, and project its recorded value"
                ),
            },
        )
    operation_names = {operation.casefold() for operation in operations}
    parameter_names = {
        "params",
        "parameters",
        "arguments",
        "request",
        "request_data",
        "payload",
        "query",
    }
    map_markers = (
        "cursor",
        "offset",
        "index",
        "operation_map",
        "responses_by_operation",
        "response_by_operation",
        "per_operation",
        "recorded_responses",
        "response_records",
    )
    for path, source in sorted(sources.items()):
        if PurePosixPath(path).suffix.casefold() != ".py":
            continue
        try:
            tree = ast.parse(source)
        except SyntaxError:
            continue
        for function in ast.walk(tree):
            if not isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            function_name = function.name.casefold()
            if not any(
                marker in function_name
                for marker in ("handle", "dispatch", "request", "command", "response")
            ):
                continue
            branch = _operation_branch(function, operation_names)
            if branch is None:
                continue
            loaded_names = {
                node.id.casefold()
                for node in ast.walk(branch)
                if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load)
            }
            response_loaded_names: set[str] = set()
            for return_node in ast.walk(branch):
                if not isinstance(return_node, ast.Return) or return_node.value is None:
                    continue
                response_loaded_names.update(
                    node.id.casefold()
                    for node in ast.walk(return_node.value)
                    if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load)
                )
            request_aliases: set[str] = set()
            for assignment in ast.walk(branch):
                if not isinstance(assignment, ast.Assign):
                    continue
                value_names = {
                    node.id.casefold()
                    for node in ast.walk(assignment.value)
                    if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load)
                }
                if not (value_names & parameter_names):
                    continue
                for target in assignment.targets:
                    if isinstance(target, ast.Name):
                        request_aliases.add(target.id.casefold())
            uses_request = bool(
                response_loaded_names & (parameter_names | request_aliases)
            )
            uses_operation_map = bool(
                loaded_names
                and any(
                    any(marker in name for marker in map_markers)
                    for name in loaded_names
                )
            )
            indexes_by_operation = any(
                isinstance(node, ast.Subscript)
                and isinstance(node.slice, ast.Name)
                and node.slice.id.casefold()
                in {"method", "operation", "op", "command", "route"}
                for node in ast.walk(branch)
            )
            gets_by_operation = any(
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "get"
                and node.args
                and isinstance(node.args[0], ast.Name)
                and node.args[0].id.casefold()
                in {"method", "operation", "op", "command", "route"}
                for node in ast.walk(branch)
            )
            if uses_request or (uses_operation_map and (indexes_by_operation or gets_by_operation)):
                if not has_index_binding and not uses_operation_map:
                    return RepairConformanceResult(
                        passed=False,
                        code="operation_response_uncorrelated",
                        reason=(
                            "task-plane fixture response is not bound to the "
                            "framework operation-response index"
                        ),
                        details={
                            "path": path,
                            "function": function.name,
                            "operation": next(iter(operations), "unknown"),
                            "required_change": (
                                "consume AWORLD_REPLAY_RESPONSE_INDEX (or an "
                                "equivalent deterministic per-operation response "
                                "map) and project its recorded value"
                            ),
                        },
                    )
                continue
            operation = next(iter(operations), "unknown")
            return RepairConformanceResult(
                passed=False,
                code="operation_response_uncorrelated",
                reason=(
                    "task-plane operation branch returns a global response without "
                    "consuming request parameters or a deterministic per-operation "
                    "recorded-response map"
                ),
                details={
                    "path": path,
                    "function": function.name,
                    "operation": operation,
                    "required_operations": list(operations),
                    "required_change": [
                        "read bounded request parameters for the observed operation",
                        "or select a response from a deterministic operation map/cursor",
                        "preserve the recorded response shape instead of returning one global container",
                    ],
                },
            )
    return None


def _operation_branch(
    function: ast.FunctionDef | ast.AsyncFunctionDef,
    operation_names: set[str],
) -> ast.If | None:
    """Find an operation-specific branch without assuming a protocol name."""

    for node in ast.walk(function):
        if not isinstance(node, ast.If):
            continue
        literals = {
            str(item.value).casefold()
            for item in ast.walk(node.test)
            if isinstance(item, ast.Constant) and isinstance(item.value, str)
        }
        if literals & operation_names:
            return node
    return None


def _diagnostic_failure_codes(
    diagnostics: Sequence[Mapping[str, object]],
) -> tuple[str, ...]:
    codes: list[str] = []
    pending: list[object] = list(diagnostics)
    visited = 0
    while pending and visited < 512:
        current = pending.pop()
        visited += 1
        if isinstance(current, Mapping):
            code = current.get("code")
            if isinstance(code, str) and code and code != "failed_gate":
                normalized = sanitize_text(code, max_chars=120)
                if normalized not in codes:
                    codes.append(normalized)
            pending.extend(current.values())
        elif isinstance(current, (list, tuple)):
            pending.extend(current)
    return tuple(codes)


def _fixture_probe_structure_failure(
    sources: Mapping[str, str],
    *,
    contract: RepairConformanceContract,
) -> RepairConformanceResult | None:
    if not contract.requires_fixture_derived_probe:
        return None
    literals: set[str] = set()
    for path, source in sources.items():
        if PurePosixPath(path).suffix.lower() not in _SOURCE_SUFFIXES:
            continue
        try:
            tree = ast.parse(source)
        except SyntaxError:
            continue
        literals.update(
            value.value
            for value in ast.walk(tree)
            if isinstance(value, ast.Constant) and isinstance(value.value, str)
        )
    gateway_keys = ("action_result", "tool_outputs")
    payload_keys = ("content", "response", "result", "output", "body", "data")
    requires_gateway_repair = (
        "late_fixture_probe_outside_recorded_payload"
        in contract.failure_codes
        or any(key in literals for key in gateway_keys)
    )
    if not requires_gateway_repair:
        return None
    missing_gateway_keys = [key for key in gateway_keys if key not in literals]
    observed_payload_keys = [key for key in payload_keys if key in literals]
    if not missing_gateway_keys and observed_payload_keys:
        return None
    return RepairConformanceResult(
        passed=False,
        code="fixture_gateway_discovery_missing",
        reason=(
            "the changed selector still lacks an explicit recorded-response "
            "gateway phase before payload scalar selection"
        ),
        details={
            "focus_candidate_id": contract.focus_candidate_id,
            "missing_gateway_keys": missing_gateway_keys,
            "required_gateway_keys": list(gateway_keys),
            "required_payload_keys": list(payload_keys),
            "observed_payload_keys": observed_payload_keys,
            "required_phase_order": [
                "discover every action_result/tool_outputs subtree",
                "then enter content/response/result/output/body/data",
                "then decode nested containers and select a non-empty leaf",
            ],
        },
    )


def _fixture_probe_derivation_violations(
    sources: Mapping[str, str],
    *,
    required: bool,
) -> list[dict[str, object]]:
    if not required:
        return []
    violations: list[dict[str, object]] = []
    for path, source in sorted(sources.items()):
        if PurePosixPath(path).suffix.lower() != ".py":
            continue
        try:
            tree = ast.parse(source)
        except SyntaxError:
            continue
        unused_payload_keys = _unused_payload_key_declarations(tree)
        if unused_payload_keys:
            selector = next(
                (
                    function
                    for function in ast.walk(tree)
                    if isinstance(
                        function,
                        (ast.FunctionDef, ast.AsyncFunctionDef),
                    )
                    and any(
                        marker in function.name.casefold()
                        for marker in (
                            "payload",
                            "select",
                            "fixture",
                            "response",
                            "probe",
                        )
                    )
                    and any(
                        isinstance(node, ast.Call)
                        and isinstance(node.func, ast.Attribute)
                        and node.func.attr in {"items", "values"}
                        for node in ast.walk(function)
                    )
                ),
                None,
            )
            if selector is not None:
                violations.append(
                    {
                        "path": path,
                        "function": selector.name,
                        "line": int(selector.lineno),
                        "construct": "payload_key_gate_declared_but_unused",
                    }
                )
        for function in ast.walk(tree):
            if not isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if not _fixture_selector_function_name(function.name):
                continue
            top_level_projection = _top_level_fixture_projection(function)
            if top_level_projection is not None:
                violations.append(
                    {
                        "path": path,
                        "function": function.name,
                        "line": int(top_level_projection.lineno),
                        "construct": "top_level_fixture_projection",
                    }
                )
            gateway_container = _gateway_container_selected_instead_of_subtree(
                function
            )
            if gateway_container is not None:
                violations.append(
                    {
                        "path": path,
                        "function": function.name,
                        "line": int(gateway_container.lineno),
                        "construct": "gateway_container_selected_instead_of_subtree",
                    }
                )
            boolean_metadata = _boolean_metadata_not_excluded(function)
            if boolean_metadata is not None:
                violations.append(
                    {
                        "path": path,
                        "function": function.name,
                        "line": int(boolean_metadata.lineno),
                        "construct": "boolean_metadata_not_excluded",
                    }
                )
            direct_gateway_scalar = _direct_gateway_scalar_selection(function)
            if direct_gateway_scalar is not None:
                violations.append(
                    {
                        "path": path,
                        "function": function.name,
                        "line": int(direct_gateway_scalar.lineno),
                        "construct": "gateway_scalar_selected_before_payload",
                    }
                )
            root_fallback = _root_fallback_reachable_after_gateway(function)
            if root_fallback is not None:
                violations.append(
                    {
                        "path": path,
                        "function": function.name,
                        "line": int(root_fallback.lineno),
                        "construct": "root_fallback_reachable_after_gateway",
                    }
                )
            skipped_sequence = _gateway_discovery_sequence_skip(function)
            if skipped_sequence is not None:
                violations.append(
                    {
                        "path": path,
                        "function": function.name,
                        "line": int(
                            getattr(skipped_sequence, "lineno", function.lineno)
                        ),
                        "construct": (
                            "gateway_discovery_skips_nested_sequences"
                        ),
                    }
                )
            docstring_node = (
                function.body[0].value
                if function.body
                and isinstance(function.body[0], ast.Expr)
                and isinstance(function.body[0].value, ast.Constant)
                and isinstance(function.body[0].value.value, str)
                else None
            )
            for node in ast.walk(function):
                if node is docstring_node:
                    continue
                construct = _forbidden_fixture_derivation_construct(node)
                if construct is None:
                    continue
                violations.append(
                    {
                        "path": path,
                        "function": function.name,
                        "line": int(getattr(node, "lineno", function.lineno)),
                        "construct": construct,
                    }
                )
    return violations[:32]


def _top_level_fixture_projection(
    function: ast.FunctionDef | ast.AsyncFunctionDef,
) -> ast.Return | ast.Call | None:
    """Reject task data helpers that expose the outer fixture envelope.

    A trajectory fixture is commonly a list of action records.  Returning
    ``FIXTURE_DATA`` or ``FIXTURE_DATA.get(key, [])`` from a task-plane helper
    is non-empty but not a recorded response: it discards the gateway/payload
    correlation and makes callers observe action metadata instead of task
    records.  This check is deliberately limited to helpers that advertise
    fixture/list/response normalization; arbitrary protocol code may still use
    a local list or a concrete response projection.
    """

    name = function.name.casefold()
    if not any(
        marker in name
        for marker in ("normalize", "fixture_list", "fixture_data", "response_data")
    ):
        return None
    fixture_names = {"fixture_data", "fixture", "root"}
    for node in ast.walk(function):
        if isinstance(node, ast.Return) and isinstance(node.value, ast.Name):
            if node.value.id.casefold() in fixture_names:
                return node
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr != "get" or not isinstance(node.func.value, ast.Name):
            continue
        if node.func.value.id.casefold() not in fixture_names:
            continue
        if node.args and isinstance(node.args[-1], ast.List):
            return node
    return None


def _gateway_container_selected_instead_of_subtree(
    function: ast.FunctionDef | ast.AsyncFunctionDef,
) -> ast.Call | None:
    """Find ``gateways.append(root)``-style container selection.

    Gateway discovery must retain the value below ``action_result`` or
    ``tool_outputs``.  Appending the surrounding root/container causes later
    scalar selection to see envelope metadata instead of the recorded payload.
    The check is intentionally structural and protocol-neutral: it only uses
    gateway-shaped names and the observed gateway key literals.
    """

    gateway_names = {
        argument.arg
        for argument in (*function.args.posonlyargs, *function.args.args)
        if "gateway" in argument.arg.casefold()
    }
    gateway_names.update(
        node.id
        for node in ast.walk(function)
        if isinstance(node, ast.Name) and "gateway" in node.id.casefold()
    )
    root_names = {
        argument.arg
        for argument in (*function.args.posonlyargs, *function.args.args)
        if argument.arg.casefold() in {"root", "obj", "value", "item"}
    }
    gateway_literals = {
        node.value
        for node in ast.walk(function)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and node.value.casefold() in {"action_result", "tool_outputs"}
    }
    if not gateway_literals or not gateway_names or not root_names:
        return None
    for call in ast.walk(function):
        if not isinstance(call, ast.Call) or not isinstance(call.func, ast.Attribute):
            continue
        if call.func.attr != "append" or not call.args:
            continue
        receiver = call.func.value
        argument = call.args[0]
        if (
            isinstance(receiver, ast.Name)
            and receiver.id in gateway_names
            and isinstance(argument, ast.Name)
            and argument.id in root_names
        ):
            return call
    return None


def _boolean_metadata_not_excluded(
    function: ast.FunctionDef | ast.AsyncFunctionDef,
) -> ast.Call | None:
    """Find scalar selectors that accept ``int`` without excluding ``bool``."""
    scalar_subjects: dict[str, ast.Call] = {}
    for call in ast.walk(function):
        if not isinstance(call, ast.Call) or not isinstance(call.func, ast.Name):
            continue
        if call.func.id != "isinstance" or len(call.args) < 2:
            continue
        subject = call.args[0]
        if not isinstance(subject, ast.Name):
            continue
        scalar_types = {
            name.id
            for name in ast.walk(call.args[1])
            if isinstance(name, ast.Name)
        }
        if not scalar_types & {"int", "float"} or "bool" in scalar_types:
            continue
        scalar_subjects.setdefault(subject.id, call)
    if not scalar_subjects:
        return None

    # A selector is conforming when it explicitly rejects/continues on bool
    # before the broad scalar check.  Infer the subject name from the actual
    # isinstance call rather than relying on conventional names such as
    # ``value``; generated candidates commonly call it ``data`` or ``node``.
    for node in ast.walk(function):
        if not isinstance(node, ast.If):
            continue
        test = node.test
        if not (
            isinstance(test, ast.Call)
            and isinstance(test.func, ast.Name)
            and test.func.id == "isinstance"
            and len(test.args) >= 2
            and isinstance(test.args[0], ast.Name)
            and any(
                isinstance(name, ast.Name) and name.id == "bool"
                for name in ast.walk(test.args[1])
            )
            and test.args[0].id in scalar_subjects
        ):
            continue
        if any(
            isinstance(statement, (ast.Return, ast.Raise, ast.Continue, ast.Break))
            for statement in node.body
        ):
            scalar_subjects.pop(test.args[0].id, None)
    return next(iter(scalar_subjects.values()), None)


def _direct_gateway_scalar_selection(
    function: ast.FunctionDef | ast.AsyncFunctionDef,
) -> ast.Call | None:
    """Find a selector that feeds a gateway itself into a scalar/leaf walk."""

    gateway_names = {
        argument.arg
        for argument in (*function.args.posonlyargs, *function.args.args)
        if argument.arg.casefold() in {"gateway", "gw"}
        or "gateway" in argument.arg.casefold()
    }
    gateway_names.update(
        node.id
        for node in ast.walk(function)
        if isinstance(node, ast.Name)
        and (
            node.id.casefold() in {"gateway", "gw"}
            or "gateway" in node.id.casefold()
        )
    )
    if not gateway_names:
        return None
    for call in ast.walk(function):
        if not isinstance(call, ast.Call) or not call.args:
            continue
        callee = call.func
        callee_name = (
            callee.id
            if isinstance(callee, ast.Name)
            else callee.attr
            if isinstance(callee, ast.Attribute)
            else ""
        ).casefold()
        if not any(marker in callee_name for marker in ("leaf", "scalar")):
            continue
        first_argument = call.args[0]
        if isinstance(first_argument, ast.Name) and first_argument.id in gateway_names:
            return call
    return None


def _root_fallback_reachable_after_gateway(
    function: ast.FunctionDef | ast.AsyncFunctionDef,
) -> ast.Call | None:
    """Find a root scalar walk reachable after a truthy gateway branch.

    A fixture selector may inspect the parsed root only when the complete gateway
    list is empty.  This catches the common rationale/source mismatch where an
    ``if gateways`` block performs the right payload walk but then falls through
    to an unconditional ``select_scalar(root)`` statement.
    """

    pending: list[list[ast.stmt]] = [function.body]
    while pending:
        statements = pending.pop()
        for index, statement in enumerate(statements):
            pending.extend(_nested_statement_blocks(statement))
            if not isinstance(statement, ast.If):
                continue
            gateway_names = _positive_gateway_test_names(statement.test)
            if not gateway_names or _block_guaranteed_terminates(statement.body):
                continue
            root_names = _gateway_source_names(
                statements[:index],
                gateway_names=gateway_names,
            )
            if not root_names:
                continue
            for following in statements[index + 1 :]:
                if _statement_guards_gateway_empty(
                    following,
                    gateway_names=gateway_names,
                ):
                    continue
                fallback = _direct_scalar_call_on_names(
                    following,
                    names=root_names,
                )
                if fallback is not None:
                    return fallback
                if _statement_guaranteed_terminates(following):
                    break
    return None


def _nested_statement_blocks(statement: ast.stmt) -> list[list[ast.stmt]]:
    blocks: list[list[ast.stmt]] = []
    for attribute in ("body", "orelse", "finalbody"):
        value = getattr(statement, attribute, None)
        if isinstance(value, list) and value:
            blocks.append(value)
    handlers = getattr(statement, "handlers", None)
    if isinstance(handlers, list):
        for handler in handlers:
            body = getattr(handler, "body", None)
            if isinstance(body, list) and body:
                blocks.append(body)
    return blocks


def _positive_gateway_test_names(test: ast.expr) -> set[str]:
    if isinstance(test, ast.UnaryOp) and isinstance(test.op, ast.Not):
        return set()
    return {
        node.id
        for node in ast.walk(test)
        if isinstance(node, ast.Name) and "gateway" in node.id.casefold()
    }


def _gateway_source_names(
    statements: Sequence[ast.stmt],
    *,
    gateway_names: set[str],
) -> set[str]:
    roots: set[str] = set()
    for statement in statements:
        for call in ast.walk(statement):
            if not isinstance(call, ast.Call):
                continue
            argument_names = {
                argument.id
                for argument in call.args
                if isinstance(argument, ast.Name)
            }
            if not (argument_names & gateway_names):
                continue
            roots.update(argument_names - gateway_names)
        if isinstance(statement, (ast.Assign, ast.AnnAssign)):
            assigned = set(_assigned_names(statement))
            value = statement.value
            if assigned & gateway_names and isinstance(value, ast.Call):
                roots.update(
                    argument.id
                    for argument in value.args
                    if isinstance(argument, ast.Name)
                    and argument.id not in gateway_names
                )
    return roots


def _statement_guards_gateway_empty(
    statement: ast.stmt,
    *,
    gateway_names: set[str],
) -> bool:
    if not isinstance(statement, ast.If):
        return False
    test = statement.test
    return (
        isinstance(test, ast.UnaryOp)
        and isinstance(test.op, ast.Not)
        and any(
            isinstance(node, ast.Name) and node.id in gateway_names
            for node in ast.walk(test.operand)
        )
    )


def _direct_scalar_call_on_names(
    statement: ast.stmt,
    *,
    names: set[str],
) -> ast.Call | None:
    # Nested conditionals have their own path predicate. They are deliberately
    # not flattened here, so an explicit ``if not gateways`` remains valid.
    if isinstance(statement, (ast.If, ast.For, ast.AsyncFor, ast.While, ast.Try)):
        return None
    for call in ast.walk(statement):
        if not isinstance(call, ast.Call) or not call.args:
            continue
        callee = call.func
        callee_name = (
            callee.id
            if isinstance(callee, ast.Name)
            else callee.attr
            if isinstance(callee, ast.Attribute)
            else ""
        ).casefold()
        if not any(marker in callee_name for marker in ("leaf", "scalar")):
            continue
        first_argument = call.args[0]
        if isinstance(first_argument, ast.Name) and first_argument.id in names:
            return call
    return None


def _block_guaranteed_terminates(statements: Sequence[ast.stmt]) -> bool:
    return any(_statement_guaranteed_terminates(statement) for statement in statements)


def _statement_guaranteed_terminates(statement: ast.stmt) -> bool:
    if isinstance(statement, (ast.Return, ast.Raise, ast.Continue, ast.Break)):
        return True
    if isinstance(statement, ast.If):
        return bool(statement.orelse) and _block_guaranteed_terminates(
            statement.body
        ) and _block_guaranteed_terminates(statement.orelse)
    return False


def _unused_payload_key_declarations(tree: ast.Module) -> set[str]:
    payload_keys = {"content", "response", "result", "output", "body", "data"}
    declared: set[str] = set()
    for node in tree.body:
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        names = _assigned_names(node)
        if not any("payload" in name.casefold() for name in names):
            continue
        value = node.value
        literals = {
            item.value
            for item in ast.walk(value)
            if isinstance(item, ast.Constant) and isinstance(item.value, str)
        }
        if payload_keys & literals:
            declared.update(names)
    loaded = {
        node.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load)
    }
    return declared - loaded


def _fixture_selector_function_name(name: str) -> bool:
    normalized = name.casefold()
    return any(
        marker in normalized
        for marker in (
            "derive",
            "extract",
            "fixture",
            "gateway",
            "payload",
            "probe",
            "recorded",
            "response",
            "scalar",
            "select",
            "token",
        )
    )


def _gateway_discovery_sequence_skip(
    function: ast.FunctionDef | ast.AsyncFunctionDef,
) -> ast.If | None:
    literals = {
        node.value
        for node in ast.walk(function)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    }
    if not {"action_result", "tool_outputs"}.issubset(literals):
        return None
    sequence_types = {"list", "tuple", "Sequence", "MutableSequence"}
    if any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "isinstance"
        and len(node.args) >= 2
        and any(
            isinstance(name, ast.Name) and name.id in sequence_types
            for name in ast.walk(node.args[1])
        )
        for node in ast.walk(function)
    ):
        return None
    for node in ast.walk(function):
        if not isinstance(node, ast.If):
            continue
        test = node.test
        if not (
            isinstance(test, ast.UnaryOp)
            and isinstance(test.op, ast.Not)
            and isinstance(test.operand, ast.Call)
            and isinstance(test.operand.func, ast.Name)
            and test.operand.func.id == "isinstance"
            and len(test.operand.args) >= 2
            and isinstance(test.operand.args[0], ast.Name)
            and any(
                isinstance(name, ast.Name) and name.id == "dict"
                for name in ast.walk(test.operand.args[1])
            )
        ):
            continue
        subject = test.operand.args[0].id
        if any(
            isinstance(value, ast.Return)
            and isinstance(value.value, ast.Name)
            and value.value.id == subject
            for statement in node.body
            for value in ast.walk(statement)
        ):
            return node
    return None


def _forbidden_fixture_derivation_construct(node: ast.AST) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        normalized = node.value.strip().casefold()
        if "placeholder" in normalized or "default_token" in normalized:
            return "literal_probe_fallback"
    if isinstance(node, ast.Call):
        function = node.func
        if isinstance(function, ast.Attribute) and function.attr in {
            "findall",
            "finditer",
            "fullmatch",
            "match",
            "search",
        }:
            return "regex_scalar_filter"
        if (
            isinstance(function, ast.Attribute)
            and function.attr.casefold().startswith(("md5", "sha"))
            and _hash_call_is_assertion_fallback(node)
        ):
            return "fixture_hash_assertion_fallback"
    if isinstance(node, ast.Compare) and _narrow_scalar_length_filter(node):
        return "narrow_scalar_length_filter"
    return None


def _hash_call_is_assertion_fallback(call: ast.Call) -> bool:
    # A hash used as a source identifier is harmless. In a selector function,
    # hashing fixture/token/scalar inputs is an assertion fallback and cannot
    # prove response reconstruction.
    return any(
        isinstance(name, ast.Name)
        and any(
            marker in name.id.casefold()
            for marker in ("fixture", "payload", "response", "scalar", "token")
        )
        for argument in call.args
        for name in ast.walk(argument)
    )


def _narrow_scalar_length_filter(compare: ast.Compare) -> bool:
    nodes = (compare.left, *compare.comparators)
    for value in nodes:
        for call in ast.walk(value):
            if (
                isinstance(call, ast.Call)
                and isinstance(call.func, ast.Name)
                and call.func.id == "len"
                and call.args
                and isinstance(call.args[0], ast.Name)
                and call.args[0].id.casefold()
                in {"candidate", "leaf", "s", "scalar", "token", "value"}
            ):
                return True
    return False


def evaluate_compiled_probe_conformance(
    services: Sequence[ReplayServiceSpec],
    contract: RepairConformanceContract,
    *,
    fixture_leaf_values: Mapping[str, Sequence[str]] | None = None,
    fixture_response_leaf_values: Mapping[str, Sequence[str]] | None = None,
) -> RepairConformanceResult:
    service_probes = tuple(
        (service, probe)
        for service in services
        if service.transport == "skill_runtime"
        for probe in service.protocol_probes
    )
    probes = tuple(probe for _, probe in service_probes)
    if contract.exact_probe is not None:
        exact = contract.exact_probe
        location_matching = [
            (service, probe)
            for service, probe in service_probes
            if probe.kind == exact.kind
            and probe.path == exact.path
            and isinstance(probe.response_contains, str)
            and bool(probe.response_contains.strip())
        ]
        recorded_values = fixture_leaf_values
        if not location_matching:
            return RepairConformanceResult(
                passed=False,
                code="exact_repair_probe_missing",
                reason=(
                    "compiled candidate does not declare the exact fixture-derived "
                    "probe required by the failed branch"
                ),
                details={
                    "probe_kind": exact.kind,
                    "probe_path": exact.path,
                    "expected_preview": exact.expected_response,
                    "declared_probe_count": len(probes),
                },
            )
        if recorded_values is None:
            matching = [
                (service, probe)
                for service, probe in location_matching
                if _fixture_value_matches(
                    exact.expected_response,
                    str(probe.response_contains),
                )
            ]
        else:
            matching = [
                (service, probe)
                for service, probe in location_matching
                if not _placeholder_probe_value(probe.response_contains)
                and any(
                    _fixture_value_matches(
                        str(probe.response_contains),
                        value,
                    )
                    for value in recorded_values.get(
                        service.response_fixture, ()
                    )
                    if isinstance(value, str) and value
                )
            ]
        if not matching:
            return RepairConformanceResult(
                passed=False,
                code=(
                    "exact_repair_probe_missing"
                    if recorded_values is None
                    else "exact_repair_probe_not_recorded"
                ),
                reason=(
                    "compiled candidate does not declare the exact fixture-derived "
                    "probe required by the failed branch"
                    if recorded_values is None
                    else (
                        "exact repair probe must use a recorded scalar value rather "
                        "than a mapping key, raw token, or hard-coded diagnostic preview"
                    )
                ),
                details={
                    "probe_kind": exact.kind,
                    "probe_path": exact.path,
                    "previous_expected_preview": exact.expected_response,
                    "matching_location_count": len(location_matching),
                    "required_reconstruction_algorithm": [
                        "parse the recorded fixture as JSON or JSONL",
                        "recursively traverse mapping values and list items",
                        "select a deterministic non-empty scalar leaf",
                        "reuse one selected leaf across probes unless distinct values are required",
                        "reuse the same selector in compiler and runtime",
                        "place the selected leaf inside the protocol result payload",
                    ],
                    "forbidden_derivations": [
                        "mapping keys",
                        "raw-byte regex tokens",
                        "hard-coded diagnostic previews",
                        "placeholder literals",
                    ],
                },
            )

    if contract.requires_fixture_derived_probe:
        required_operations = (
            contract.required_fixture_probe_operations
            or contract.late_observed_operations[-1:]
        )
        if not required_operations:
            # A task-plane repair without an observed operation cannot prove
            # that its probe reaches the failed branch.  Treat missing trace
            # evidence as a typed conformance failure instead of allowing an
            # arbitrary readiness/data-plane probe to pass vacuously.
            return RepairConformanceResult(
                passed=False,
                code="late_observed_operation_missing",
                reason=(
                    "task-plane repair requires at least one observed operation "
                    "to bind a non-empty fixture-derived probe"
                ),
                details={
                    "late_observed_operations": list(
                        contract.late_observed_operations
                    ),
                    "required_fixture_probe_operations": list(
                        contract.required_fixture_probe_operations
                    ),
                    "declared_probe_count": len(probes),
                },
            )
        matching_by_operation = {
            operation: [
                (service, probe)
                for service, probe in service_probes
                if isinstance(probe.request_text, str)
                and _request_covers_operation(probe.request_text, operation)
                and isinstance(probe.response_contains, str)
                and bool(probe.response_contains.strip())
            ]
            for operation in required_operations
        }
        missing_operations = [
            operation
            for operation, matching_probes in matching_by_operation.items()
            if not matching_probes
        ]
        if missing_operations:
            return RepairConformanceResult(
                passed=False,
                code="late_fixture_probe_missing",
                reason=(
                    "task-plane repair must declare a non-empty fixture-derived probe "
                    "covering the latest observed operation"
                ),
                details={
                    "required_probe_operation": missing_operations[-1],
                    "missing_probe_operations": missing_operations,
                    "latest_observed_operation": required_operations[-1],
                    "required_fixture_probe_operations": list(
                        contract.required_fixture_probe_operations
                    ),
                    "late_observed_operations": list(
                        contract.late_observed_operations
                    ),
                    "interaction_progress": contract.interaction_progress,
                    "declared_probe_count": len(probes),
                },
            )
        matching = [
            item
            for operation in required_operations
            for item in matching_by_operation[operation]
        ]
        recorded_values = fixture_leaf_values or {}
        response_values = fixture_response_leaf_values or {}
        # ``None`` means the caller has no response-context evidence (the
        # backwards-compatible unit-test path).  Once the capability compiler
        # supplies a response map, an empty/missing fixture entry is a hard
        # failure: falling back to all fixture leaves would allow request or
        # envelope metadata to masquerade as recorded task output.
        response_context_supplied = fixture_response_leaf_values is not None

        def response_context_for(service: ReplayServiceSpec) -> tuple[str, ...]:
            if response_context_supplied:
                return tuple(response_values.get(service.response_fixture, ()))
            return tuple(recorded_values.get(service.response_fixture, ()))

        missing_recorded_operations = [
            operation
            for operation, matching_probes in matching_by_operation.items()
            if not any(
                not _placeholder_probe_value(probe.response_contains)
                and any(
                    _fixture_value_matches(probe.response_contains, value)
                    for value in response_context_for(service)
                    if isinstance(value, str) and value
                )
                for service, probe in matching_probes
            )
        ]
        if missing_recorded_operations:
            outside_payload_matches = [
                probe.response_contains
                for service, probe in matching
                if isinstance(probe.response_contains, str)
                and probe.response_contains
                and any(
                    _fixture_value_matches(probe.response_contains, value)
                    for value in recorded_values.get(service.response_fixture, ())
                    if isinstance(value, str) and value
                )
                and not any(
                    _fixture_value_matches(probe.response_contains, value)
                    for value in response_context_for(service)
                    if isinstance(value, str) and value
                )
            ]
            return RepairConformanceResult(
                passed=False,
                code=(
                    "late_fixture_probe_outside_recorded_payload"
                    if outside_payload_matches
                    else "late_fixture_probe_not_recorded"
                ),
                reason=(
                    "task-plane repair probe selected a real fixture scalar outside "
                    "the recorded payload; perform gateway discovery first and never "
                    "select request/action or action-result metadata"
                    if outside_payload_matches
                    else (
                        "task-plane repair probe must recursively decode JSON/JSONL and "
                        "JSON-encoded output containers, then select a deterministic "
                        "non-empty recorded response leaf; request/envelope scalars, "
                        "mapping keys, and raw-byte regex tokens do not prove fixture "
                        "reconstruction"
                    )
                ),
                details={
                    "latest_observed_operation": required_operations[-1],
                    "missing_recorded_probe_operations": (
                        missing_recorded_operations
                    ),
                    "matching_probe_count": len(matching),
                    "declared_response_contains": [
                        probe.response_contains
                        for _, probe in matching[:16]
                        if probe.response_contains
                    ],
                    "declared_value_classification": (
                        "fixture_scalar_outside_recorded_payload"
                        if outside_payload_matches
                        else "not_a_recorded_fixture_scalar"
                    ),
                    "fixture_leaf_counts": {
                        path: len(values)
                        for path, values in recorded_values.items()
                    },
                    "recorded_response_leaf_counts": {
                        path: len(values)
                        for path, values in response_values.items()
                    },
                    "required_reconstruction_algorithm": [
                        "parse the recorded fixture as JSON or JSONL",
                        "recursively decode bounded JSON object or array strings",
                        "search arbitrary fixture nesting with a bounded node count rather than a shallow depth cutoff",
                        "use a gateway-discovery pass before scalar selection and never fall back to non-output trajectory branches when a gateway exists",
                        "during discovery collect gateway subtrees only: never collect or return any scalar until the complete gateway list is known",
                        "keep trajectory gateway keys limited to action_result and tool_outputs; treat content, response, result, output, body, and data only as payload keys after a gateway",
                        "when gateways exist, call the payload collector on each gateway and call the scalar selector only on those payload subtrees; never scalar-walk a gateway directly",
                        "treat payload selection inside gateways as phase 2; only use a generic parsed-root fallback when the complete gateway list is empty",
                        "recursively traverse mapping values and list items",
                        "for trajectory envelopes enter through action_result or tool_outputs at any depth, then ignore action-result metadata until reaching a content, response, result, output, body, or data payload",
                        "when a gateway value is a list, apply payload-key selection to each item instead of sending the whole list to generic scalar traversal",
                        "select a deterministic non-empty scalar leaf without arbitrary alphanumeric or narrow length filters",
                        "reuse one selected leaf across probes unless distinct values are required",
                        "reuse the same selector in compiler and runtime",
                        "return the surrounding decoded recorded container in the protocol result payload",
                        "choose probe request inputs that execute the fixture-derived handler branch rather than a constant-result branch",
                    ],
                    "forbidden_derivations": [
                        "mapping keys",
                        "raw-byte regex tokens",
                        "request or envelope-only scalar values when recorded output values exist",
                        "action-result metadata such as tool names, call ids, success flags, or timing fields",
                        "hash or placeholder fallbacks when no leaf matches an arbitrary token regex",
                        "globally treating result or output keys in trajectory request/action records as recorded responses",
                        "placeholder literals",
                        "empty arrays or objects",
                    ],
                },
            )

    return _passed(
        "repair_probes_conform",
        "compiled candidate declares the required repair probes",
        declared_probe_count=len(probes),
    )


def _diagnostic_mappings(value: Mapping[str, object]) -> Sequence[Mapping[str, object]]:
    raw = value.get("candidate_validation_diagnostics")
    if not isinstance(raw, list):
        return ()
    return tuple(item for item in raw[:32] if isinstance(item, Mapping))


def _inherited_repair_conformance_contract(
    diagnostics: Sequence[Mapping[str, object]],
) -> RepairConformanceContract | None:
    inherited: list[RepairConformanceContract] = []

    def collect(value: object) -> None:
        if isinstance(value, Mapping):
            raw_contract = value.get("repair_conformance")
            if isinstance(raw_contract, Mapping):
                contract = RepairConformanceContract.from_dict(raw_contract)
                if contract.focus_candidate_id:
                    inherited.append(contract)
            for key, nested in value.items():
                if key == "repair_conformance":
                    continue
                if isinstance(nested, (Mapping, list, tuple)):
                    collect(nested)
        elif isinstance(value, (list, tuple)):
            for nested in value:
                collect(nested)

    collect(diagnostics)
    return inherited[-1] if inherited else None


def _exact_probe_constraint(
    diagnostics: Sequence[Mapping[str, object]],
) -> ExactRepairProbe | None:
    for item in diagnostics:
        if item.get("code") != "verify_declared_protocol_probe_branch":
            continue
        kind = item.get("probe_kind")
        path = item.get("probe_path")
        expected = item.get("expected_preview")
        if not all(isinstance(value, str) and value for value in (kind, path, expected)):
            continue
        if expected == "unknown":
            continue
        return ExactRepairProbe(
            kind=sanitize_text(kind, max_chars=40),
            path=sanitize_text(path, max_chars=160),
            expected_response=sanitize_text(expected, max_chars=160),
        )
    return None


def _observed_operations(
    diagnostics: Sequence[Mapping[str, object]],
) -> tuple[str, ...]:
    operations: list[str] = []
    def collect(item: object) -> None:
        if isinstance(item, Mapping):
            raw = item.get("observed_request_operations")
            if isinstance(raw, list):
                for value in raw:
                    if not isinstance(value, str) or not value.strip():
                        continue
                    normalized = sanitize_text(value, max_chars=120).strip()
                    if normalized in operations:
                        operations.remove(normalized)
                    operations.append(normalized)
            for value in item.values():
                if isinstance(value, (Mapping, list, tuple)):
                    collect(value)
        elif isinstance(item, (list, tuple)):
            for value in item:
                collect(value)

    collect(diagnostics)
    return tuple(operations[-_MAX_OBSERVED_OPERATIONS:])


def _base_branch_fingerprints(
    sources: Mapping[str, str],
    *,
    branch_paths: Sequence[str],
    markers: Sequence[str],
) -> dict[str, str]:
    fingerprints: dict[str, str] = {}
    for path in branch_paths:
        source = sources.get(path)
        if not isinstance(source, str):
            continue
        for marker in markers:
            branch_slice = _source_branch_slice(source, marker)
            if not branch_slice:
                continue
            fingerprints[_branch_key(path, marker)] = _source_fingerprint(
                branch_slice
            )
    return fingerprints


def _changed_branch_slices(
    candidate_sources: Mapping[str, str],
    base_fingerprints: Mapping[str, str],
) -> list[str]:
    changed: list[str] = []
    for key, base_fingerprint in base_fingerprints.items():
        path, separator, marker = key.partition("\n")
        if not separator:
            continue
        # Candidate sources are deltas.  An omitted path inherits the baseline
        # file and is not evidence that this branch changed.
        if path not in candidate_sources:
            continue
        candidate_source = candidate_sources[path]
        candidate_fingerprint = _source_fingerprint(
            _source_branch_slice(candidate_source, marker)
        )
        if candidate_fingerprint != base_fingerprint:
            changed.append(f"{path}#{marker}")
    return changed


def _fixture_selector_fingerprints(
    sources: Mapping[str, str],
    *,
    branch_paths: Sequence[str] | None = None,
    markers: Sequence[str] = (),
) -> dict[str, str]:
    fingerprints: dict[str, str] = {}
    selected_paths = set(branch_paths) if branch_paths is not None else None
    for path, source in sorted(sources.items()):
        if PurePosixPath(path).suffix.lower() != ".py":
            continue
        if selected_paths is not None and path not in selected_paths:
            continue
        try:
            tree = ast.parse(source)
        except SyntaxError:
            continue
        relevant_names = (
            _relevant_python_dependency_functions(tree, source, markers)
            if markers
            else None
        )
        for node in tree.body:
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if not _fixture_selector_function_name(node.name):
                continue
            if relevant_names is not None and node.name not in relevant_names:
                continue
            segment = ast.get_source_segment(source, node) or ""
            fingerprints[_branch_key(path, node.name)] = _source_fingerprint(
                segment
            )
    return fingerprints


def _relevant_python_dependency_functions(
    tree: ast.Module,
    source: str,
    markers: Sequence[str],
) -> set[str]:
    functions = {
        node.name: node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    top_assignments: dict[str, list[ast.AST]] = {}
    for node in tree.body:
        if isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
            for name in _assigned_names(node):
                top_assignments.setdefault(name, []).append(node)

    parent_by_node: dict[ast.AST, ast.AST] = {}
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            parent_by_node[child] = parent

    seed_nodes: list[ast.stmt] = []
    enclosing_functions: set[str] = set()
    for marker in markers:
        candidates: list[tuple[int, ast.stmt]] = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.stmt) or not hasattr(node, "end_lineno"):
                continue
            segment = ast.get_source_segment(source, node) or ""
            if marker not in segment:
                continue
            span = int(getattr(node, "end_lineno", node.lineno)) - node.lineno
            candidates.append((span, node))
        if not candidates:
            continue
        _, seed = min(candidates, key=lambda item: item[0])
        seed_nodes.append(seed)
        parent: ast.AST | None = seed
        while parent is not None:
            if isinstance(parent, (ast.FunctionDef, ast.AsyncFunctionDef)):
                enclosing_functions.add(parent.name)
                break
            parent = parent_by_node.get(parent)

    local_assignments: dict[str, list[ast.AST]] = {}
    for function_name in enclosing_functions:
        function = functions.get(function_name)
        if function is None:
            continue
        for node in ast.walk(function):
            if isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
                for name in _assigned_names(node):
                    local_assignments.setdefault(name, []).append(node)

    global_mutators: dict[str, list[ast.AST]] = {}
    for function in functions.values():
        declared_globals = {
            name
            for node in ast.walk(function)
            if isinstance(node, ast.Global)
            for name in node.names
        }
        assigned = {
            name
            for node in ast.walk(function)
            if isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign))
            for name in _assigned_names(node)
        }
        for name in declared_globals & assigned:
            global_mutators.setdefault(name, []).append(function)

    pending = list(_loaded_names(seed_nodes))
    visited_names: set[str] = set()
    relevant_functions: set[str] = set()
    while pending and len(visited_names) < 512:
        name = pending.pop()
        if name in visited_names:
            continue
        visited_names.add(name)
        dependencies: list[ast.AST] = []
        function = functions.get(name)
        if function is not None:
            relevant_functions.add(name)
            dependencies.append(function)
        dependencies.extend(top_assignments.get(name, ()))
        dependencies.extend(local_assignments.get(name, ()))
        for mutator in global_mutators.get(name, ()):
            if isinstance(mutator, (ast.FunctionDef, ast.AsyncFunctionDef)):
                relevant_functions.add(mutator.name)
            dependencies.append(mutator)
        pending.extend(_loaded_names(dependencies))
    return relevant_functions


def _assigned_names(node: ast.AST) -> set[str]:
    targets: list[ast.AST] = []
    if isinstance(node, ast.Assign):
        targets.extend(node.targets)
    elif isinstance(node, (ast.AnnAssign, ast.AugAssign)):
        targets.append(node.target)
    return {
        value.id
        for target in targets
        for value in ast.walk(target)
        if isinstance(value, ast.Name)
    }


def _loaded_names(nodes: Sequence[ast.AST]) -> set[str]:
    return {
        value.id
        for node in nodes
        for value in ast.walk(node)
        if isinstance(value, ast.Name) and isinstance(value.ctx, ast.Load)
    }


def _changed_fixture_selector_slices(
    candidate_sources: Mapping[str, str],
    base_fingerprints: Mapping[str, str],
) -> list[str]:
    candidate_fingerprints = _fixture_selector_fingerprints(candidate_sources)
    changed: list[str] = []
    for key, base_fingerprint in base_fingerprints.items():
        path, separator, function_name = key.partition("\n")
        if not separator:
            continue
        # A selector in an omitted file is still the baseline selector.  Only
        # compare fingerprints for files explicitly supplied by the candidate;
        # a deleted file is handled by the focused-branch deletion check above.
        if path not in candidate_sources:
            continue
        if candidate_fingerprints.get(key) != base_fingerprint:
            changed.append(f"{path}#{function_name}")
    return changed


def _source_branch_slice(source: str, marker: str) -> str:
    try:
        tree = ast.parse(source)
    except SyntaxError:
        tree = None
    if tree is not None:
        candidates: list[tuple[int, ast.stmt, str]] = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.stmt) or not hasattr(node, "end_lineno"):
                continue
            segment = ast.get_source_segment(source, node) or ""
            if marker not in segment:
                continue
            line_span = int(getattr(node, "end_lineno", node.lineno)) - node.lineno
            candidates.append((line_span, node, segment))
        if candidates:
            _, node, branch = min(candidates, key=lambda item: item[0])
            called_names = {
                call.func.id
                for call in ast.walk(node)
                if isinstance(call, ast.Call) and isinstance(call.func, ast.Name)
            }
            dependencies = [
                block
                for name in sorted(called_names)
                if (block := _top_level_python_definition(source, name))
            ]
            return "\n".join((branch, *dependencies))

    lines = source.splitlines()
    marker_indexes = [
        index for index, line in enumerate(lines) if marker in line
    ]
    if not marker_indexes:
        return ""
    selected_lines: list[str] = []
    for index in marker_indexes[:8]:
        selected_lines.extend(lines[max(0, index - 3) : min(len(lines), index + 6)])
    window = "\n".join(selected_lines)
    called_names = {
        match.group(1)
        for match in re.finditer(r"\b([A-Za-z_]\w*)\s*\(", window)
        if match.group(1) not in {"if", "for", "return", "while"}
    }
    dependencies = [
        block
        for name in sorted(called_names)
        if (block := _top_level_python_definition(source, name))
    ]
    return "\n".join((window, *dependencies))


def _top_level_python_definition(source: str, name: str) -> str:
    pattern = re.compile(
        rf"(?m)^(?:async\s+def|def)\s+{re.escape(name)}\s*\("
    )
    match = pattern.search(source)
    if match is None:
        return ""
    next_definition = re.search(
        r"(?m)^(?:async\s+def|def|class)\s+[A-Za-z_]\w*",
        source[match.end() :],
    )
    end = (
        match.end() + next_definition.start()
        if next_definition is not None
        else len(source)
    )
    return source[match.start() : end].rstrip()


def _branch_key(path: str, marker: str) -> str:
    return f"{path}\n{marker}"


def _replay_implementation_paths(
    sources: Mapping[str, str],
) -> tuple[str | None, tuple[str, ...]]:
    for path, content in sources.items():
        if PurePosixPath(path).suffix.lower() != ".json":
            continue
        try:
            manifest = json.loads(content)
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        if (
            not isinstance(manifest, Mapping)
            or manifest.get("schema_version") != REPLAY_CAPABILITY_SCHEMA_VERSION
        ):
            continue
        raw_runtime_files = manifest.get("runtime_files")
        runtime_files = tuple(
            normalized
            for value in raw_runtime_files
            if (normalized := _bounded_relative_path(value)) is not None
        ) if isinstance(raw_runtime_files, list) else ()
        return path, tuple(dict.fromkeys(runtime_files))

    fallback = tuple(
        path
        for path in sources
        if PurePosixPath(path).suffix.lower() in _SOURCE_SUFFIXES
    )
    return None, fallback


def _request_covers_operation(request_text: str, operation: str) -> bool:
    try:
        payload = json.loads(request_text)
    except (TypeError, ValueError, json.JSONDecodeError):
        return operation in request_text
    pending: list[Any] = [payload]
    operation_keys = {"action", "command", "method", "operation", "path", "route"}
    while pending:
        current = pending.pop()
        if isinstance(current, Mapping):
            for key, value in current.items():
                if str(key).lower() in operation_keys and value == operation:
                    return True
                if isinstance(value, (Mapping, list, tuple)):
                    pending.append(value)
        elif isinstance(current, (list, tuple)):
            pending.extend(current)
    return False


def _placeholder_probe_value(value: str | None) -> bool:
    if not isinstance(value, str):
        return True
    normalized = value.strip().casefold()
    return normalized in {
        "",
        "[]",
        "{}",
        "null",
        "none",
        "placeholder",
        "replay_placeholder",
    }


def _fixture_value_matches(expected: str, recorded: str) -> bool:
    """Match a probe assertion to a recorded leaf or decoded container.

    Substring matching is unsafe here: a mapping key such as ``result`` would
    match every encoded response object.  Scalar assertions therefore require
    exact text equality, while JSON object/array assertions may differ only in
    serialization whitespace and must compare as decoded values.
    """

    if not isinstance(expected, str) or not isinstance(recorded, str):
        return False
    if expected == recorded:
        return True
    expected_text = expected.strip()
    recorded_text = recorded.strip()
    if not expected_text or not recorded_text:
        return False
    if expected_text[:1] not in "[{" or recorded_text[:1] not in "[{":
        return False
    try:
        return json.loads(expected_text) == json.loads(recorded_text)
    except (TypeError, ValueError, json.JSONDecodeError):
        return False


def _source_fingerprint(content: str) -> str:
    return "sha256:" + hashlib.sha256(
        _canonical_source(content).encode("utf-8")
    ).hexdigest()


def _canonical_source(content: str) -> str:
    return content.replace("\r\n", "\n").replace("\r", "\n").rstrip()


def _bounded_relative_path(value: object) -> str | None:
    if not isinstance(value, str) or not value or len(value) > 400:
        return None
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts:
        return None
    normalized = path.as_posix()
    return sanitize_path_ref(normalized) if normalized not in {"", "."} else None


def _non_negative_int(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0
    return max(0, int(value))


def _string_tuple(value: object) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    return tuple(str(item) for item in value if isinstance(item, str) and item)


def _passed(code: str, reason: str, **details: object) -> RepairConformanceResult:
    return RepairConformanceResult(
        passed=True,
        code=code,
        reason=reason,
        details=details,
    )
