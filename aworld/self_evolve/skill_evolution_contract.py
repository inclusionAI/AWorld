from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence


SKILL_EVOLUTION_CONTRACT_SCHEMA_VERSION = (
    "aworld.self_evolve.skill_evolution_contract.v1"
)
SKILL_EVOLUTION_PROGRESS_SCHEMA_VERSION = (
    "aworld.self_evolve.skill_evolution_progress.v1"
)
_ID_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,159}$")


def _fingerprint(value: Mapping[str, object]) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(payload).hexdigest()}"


def _non_empty_string(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")
    return value.strip()


def _string_tuple(value: object, field_name: str) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        raise ValueError(f"{field_name} must be a string array")
    result = tuple(_non_empty_string(item, field_name) for item in value)
    if len(result) != len(set(result)):
        raise ValueError(f"{field_name} must not contain duplicates")
    return result


@dataclass(frozen=True)
class SkillCapabilityObjective:
    capability_id: str
    description: str
    case_ids: tuple[str, ...]
    required: bool = True

    def __post_init__(self) -> None:
        if not _ID_RE.fullmatch(self.capability_id):
            raise ValueError("capability_id has an invalid format")
        if not self.description.strip():
            raise ValueError("capability description must be non-empty")
        if not self.case_ids:
            raise ValueError("capability case_ids must be non-empty")
        if len(self.case_ids) != len(set(self.case_ids)):
            raise ValueError("capability case_ids must be unique")
        if not isinstance(self.required, bool):
            raise ValueError("capability required must be boolean")

    def to_dict(self) -> dict[str, object]:
        return {
            "capability_id": self.capability_id,
            "description": self.description,
            "case_ids": list(self.case_ids),
            "required": self.required,
        }

    @classmethod
    def from_dict(
        cls, value: Mapping[str, object]
    ) -> "SkillCapabilityObjective":
        unknown = set(value) - {
            "capability_id",
            "description",
            "case_ids",
            "required",
        }
        if unknown:
            raise ValueError(
                "unknown skill capability fields: " + ", ".join(sorted(unknown))
            )
        required = value.get("required", True)
        if not isinstance(required, bool):
            raise ValueError("capability required must be boolean")
        return cls(
            capability_id=_non_empty_string(
                value.get("capability_id"), "capability_id"
            ),
            description=_non_empty_string(
                value.get("description"), "capability description"
            ),
            case_ids=_string_tuple(value.get("case_ids"), "capability case_ids"),
            required=required,
        )


@dataclass(frozen=True)
class SkillEvolutionContract:
    target_skill_id: str
    objective: str
    capabilities: tuple[SkillCapabilityObjective, ...]
    preserved_invariants: tuple[str, ...] = ()
    minimum_required_coverage: float = 1.0
    required_stable_cycles: int = 1
    schema_version: str = SKILL_EVOLUTION_CONTRACT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != SKILL_EVOLUTION_CONTRACT_SCHEMA_VERSION:
            raise ValueError("unsupported skill evolution contract schema")
        if not _ID_RE.fullmatch(self.target_skill_id):
            raise ValueError("target_skill_id has an invalid format")
        if not self.objective.strip():
            raise ValueError("skill evolution objective must be non-empty")
        if not self.capabilities:
            raise ValueError("skill evolution contract requires capabilities")
        capability_ids = tuple(item.capability_id for item in self.capabilities)
        if len(capability_ids) != len(set(capability_ids)):
            raise ValueError("skill capability IDs must be unique")
        if not any(item.required for item in self.capabilities):
            raise ValueError("skill evolution contract requires one required capability")
        if not 0 < float(self.minimum_required_coverage) <= 1:
            raise ValueError("minimum_required_coverage must be in (0, 1]")
        if (
            isinstance(self.required_stable_cycles, bool)
            or not isinstance(self.required_stable_cycles, int)
            or self.required_stable_cycles <= 0
        ):
            raise ValueError("required_stable_cycles must be positive")

    @property
    def fingerprint(self) -> str:
        return _fingerprint(self.to_dict())

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "target_skill_id": self.target_skill_id,
            "objective": self.objective,
            "capabilities": [item.to_dict() for item in self.capabilities],
            "preserved_invariants": list(self.preserved_invariants),
            "minimum_required_coverage": self.minimum_required_coverage,
            "required_stable_cycles": self.required_stable_cycles,
        }

    def validate_run(
        self,
        *,
        target_type: str,
        target_id: str,
        dataset_case_ids: Sequence[str],
    ) -> None:
        if target_type != "skill" or target_id != self.target_skill_id:
            raise ValueError(
                "skill evolution contract target does not match resolved target"
            )
        available = set(dataset_case_ids)
        missing = sorted(
            {
                case_id
                for capability in self.capabilities
                for case_id in capability.case_ids
                if case_id not in available
            }
        )
        if missing:
            raise ValueError(
                "skill evolution contract references unknown dataset cases: "
                + ", ".join(missing)
            )

    def prompt_projection(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "contract_fingerprint": self.fingerprint,
            "target_skill_id": self.target_skill_id,
            "objective": self.objective,
            "capabilities": [item.to_dict() for item in self.capabilities],
            "preserved_invariants": list(self.preserved_invariants),
            "acceptance": {
                "minimum_required_coverage": self.minimum_required_coverage,
                "required_stable_cycles": self.required_stable_cycles,
                "candidate_activation_attestation_required": True,
            },
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "SkillEvolutionContract":
        unknown = set(value) - {
            "schema_version",
            "target_skill_id",
            "objective",
            "capabilities",
            "preserved_invariants",
            "minimum_required_coverage",
            "required_stable_cycles",
        }
        if unknown:
            raise ValueError(
                "unknown skill evolution contract fields: "
                + ", ".join(sorted(unknown))
            )
        raw_capabilities = value.get("capabilities")
        if not isinstance(raw_capabilities, (list, tuple)):
            raise ValueError("skill evolution capabilities must be an array")
        capabilities = tuple(
            SkillCapabilityObjective.from_dict(item)
            for item in raw_capabilities
            if isinstance(item, Mapping)
        )
        if len(capabilities) != len(raw_capabilities):
            raise ValueError("each skill evolution capability must be an object")
        raw_coverage = value.get("minimum_required_coverage", 1.0)
        if isinstance(raw_coverage, bool) or not isinstance(
            raw_coverage, (int, float)
        ):
            raise ValueError("minimum_required_coverage must be numeric")
        raw_stable_cycles = value.get("required_stable_cycles", 1)
        if isinstance(raw_stable_cycles, bool) or not isinstance(
            raw_stable_cycles, int
        ):
            raise ValueError("required_stable_cycles must be an integer")
        schema_version = value.get(
            "schema_version", SKILL_EVOLUTION_CONTRACT_SCHEMA_VERSION
        )
        return cls(
            target_skill_id=_non_empty_string(
                value.get("target_skill_id"), "target_skill_id"
            ),
            objective=_non_empty_string(value.get("objective"), "objective"),
            capabilities=capabilities,
            preserved_invariants=_string_tuple(
                value.get("preserved_invariants", ()),
                "preserved_invariants",
            ),
            minimum_required_coverage=float(raw_coverage),
            required_stable_cycles=raw_stable_cycles,
            schema_version=_non_empty_string(schema_version, "schema_version"),
        )


def load_skill_evolution_contract(
    path: str | Path,
    *,
    workspace_root: str | Path | None = None,
) -> SkillEvolutionContract:
    contract_path = Path(path).expanduser()
    if not contract_path.is_absolute() and workspace_root is not None:
        contract_path = Path(workspace_root) / contract_path
    if contract_path.is_symlink():
        raise ValueError("skill evolution contract must not be a symlink")
    contract_path = contract_path.resolve()
    if not contract_path.is_file():
        raise ValueError("skill evolution contract must be a regular JSON file")
    payload = contract_path.read_bytes()
    if len(payload) > 1_000_000:
        raise ValueError("skill evolution contract exceeds the byte limit")
    try:
        def reject_duplicate_keys(
            pairs: list[tuple[str, object]],
        ) -> dict[str, object]:
            result: dict[str, object] = {}
            for key, item in pairs:
                if key in result:
                    raise ValueError(
                        "skill evolution contract contains duplicate JSON keys"
                    )
                result[key] = item
            return result

        value = json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=reject_duplicate_keys,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("skill evolution contract must be valid UTF-8 JSON") from exc
    if not isinstance(value, Mapping):
        raise ValueError("skill evolution contract must be a JSON object")
    return SkillEvolutionContract.from_dict(value)


def evaluate_skill_evolution_replay(
    contract: SkillEvolutionContract,
    replay_result: Any,
    *,
    candidate_intervention_observed: bool | None = None,
) -> dict[str, object]:
    members = {
        str(member.case_id): member
        for member in tuple(getattr(replay_result, "member_results", ()) or ())
    }
    capability_results: list[dict[str, object]] = []
    covered_required = 0
    required_count = sum(1 for item in contract.capabilities if item.required)
    for capability in contract.capabilities:
        case_results: list[dict[str, object]] = []
        covered = True
        for case_id in capability.case_ids:
            member = members.get(case_id)
            candidate = getattr(member, "candidate", None)
            metrics = dict(getattr(candidate, "metrics", {}) or {})
            intervention_observed = (
                candidate_intervention_observed
                if candidate_intervention_observed is not None
                else metrics.get("candidate_intervention_observed") is True
            )
            case_covered = bool(
                candidate is not None
                and getattr(candidate, "status", None) == "succeeded"
                and metrics.get("skill_activation_attested") is True
                and intervention_observed
            )
            covered = covered and case_covered
            case_results.append(
                {
                    "case_id": case_id,
                    "covered": case_covered,
                    "candidate_status": getattr(candidate, "status", None),
                    "skill_activation_attested": (
                        metrics.get("skill_activation_attested") is True
                    ),
                    "activated_skill_package_fingerprint": metrics.get(
                        "activated_skill_package_fingerprint"
                    ),
                    "candidate_intervention_observed": (
                        intervention_observed
                    ),
                }
            )
        if capability.required and covered:
            covered_required += 1
        capability_results.append(
            {
                **capability.to_dict(),
                "covered": covered,
                "case_results": case_results,
            }
        )
    coverage = covered_required / required_count
    coverage_satisfied = coverage >= contract.minimum_required_coverage
    return {
        "schema_version": SKILL_EVOLUTION_PROGRESS_SCHEMA_VERSION,
        "contract_fingerprint": contract.fingerprint,
        "target_skill_id": contract.target_skill_id,
        "required_capability_count": required_count,
        "covered_required_capability_count": covered_required,
        "required_coverage": coverage,
        "minimum_required_coverage": contract.minimum_required_coverage,
        "coverage_satisfied": coverage_satisfied,
        "required_stable_cycles": contract.required_stable_cycles,
        "covered_capability_ids": [
            item["capability_id"]
            for item in capability_results
            if item["covered"] is True
        ],
        "missing_required_capability_ids": [
            item["capability_id"]
            for item in capability_results
            if item["required"] is True and item["covered"] is not True
        ],
        "capabilities": capability_results,
    }
