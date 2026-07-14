from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Iterable, Mapping

from aworld.self_evolve.trace_pack import (
    TracePack,
    TrajectoryLogRecord,
    build_trace_pack,
    load_trajectory_log_records,
)
from aworld.self_evolve.trajectory_context import (
    TrajectoryContextSnapshot,
    build_trajectory_context_snapshots,
    context_snapshot_for_current_trajectory,
    input_with_reconstructed_context,
)
from aworld.self_evolve.types import DatasetRecipe


SUPPORTED_SOURCE_KINDS = {
    "current_trajectory",
    "trajectory_log",
    "trajectory_set",
    "session",
    "jsonl",
    "batch_config",
}

TRAJECTORY_SET_SCHEMA_VERSION = "aworld.self_evolve.trajectory_set.v1"
TRAJECTORY_SET_MEMBER_ROLES = {
    "baseline",
    "candidate_replay",
    "accepted_followup",
    "rejected_candidate",
    "operator_added",
}
USER_AUTHORED_TRAJECTORY_SET_MEMBER_ROLES = {
    "baseline",
    "operator_added",
}
TRAJECTORY_SET_MAX_MEMBERS = 100


@dataclass(frozen=True)
class SelfEvolveEvalSourceConfig:
    kind: str = "current_trajectory"
    path: str | None = None
    session_id: str | None = None
    task_ids: tuple[str, ...] = ()
    max_cases: int = 100

    def __post_init__(self) -> None:
        if self.kind not in SUPPORTED_SOURCE_KINDS:
            raise ValueError(f"unsupported eval source kind: {self.kind}")
        if self.max_cases <= 0:
            raise ValueError("max_cases must be positive")
        object.__setattr__(self, "task_ids", tuple(self.task_ids))


@dataclass(frozen=True)
class EvalCase:
    case_id: str
    input: Any
    expected_output: Any | None = None
    verification_command: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)
    trace_pack: TracePack | None = None
    source: Mapping[str, Any] = field(default_factory=dict)
    context_snapshot: TrajectoryContextSnapshot | None = None


@dataclass(frozen=True)
class SelfEvolveDataset:
    cases: tuple[EvalCase, ...]
    recipe: DatasetRecipe


def load_jsonl_eval_cases(
    path: str | Path,
    *,
    max_cases: int | None = None,
) -> list[EvalCase]:
    resolved_path = Path(path).expanduser()
    cases: list[EvalCase] = []
    for line_number, raw_line in enumerate(
        resolved_path.read_text(encoding="utf-8").splitlines(),
        start=1,
    ):
        if max_cases is not None and len(cases) >= max_cases:
            break
        if not raw_line.strip():
            continue
        payload = json.loads(raw_line)
        if not isinstance(payload, Mapping):
            raise ValueError(f"jsonl line {line_number} must be an object")
        case_id = _case_id(payload, resolved_path=resolved_path, line_number=line_number)
        cases.append(
            EvalCase(
                case_id=case_id,
                input=payload.get("input"),
                expected_output=payload.get("expected_output"),
                verification_command=_string_or_none(payload.get("verification_command")),
                metadata=_mapping_or_empty(payload.get("metadata")),
                source={
                    "kind": "jsonl",
                    "path": str(resolved_path),
                    "line_number": line_number,
                },
            )
        )
    return cases


def build_dataset_from_source(
    source_config: SelfEvolveEvalSourceConfig,
    *,
    current_trajectory: Iterable[Mapping[str, Any]] | None = None,
    task_id: str | None = None,
    split_seed: str = "self-evolve-default-split",
) -> SelfEvolveDataset:
    if source_config.kind == "jsonl":
        if source_config.path is None:
            raise ValueError("jsonl eval source requires path")
        cases = _filter_and_limit_cases(
            load_jsonl_eval_cases(source_config.path),
            source_config=source_config,
        )
        return SelfEvolveDataset(
            cases=cases,
            recipe=build_dataset_recipe(
                cases,
                source_config=source_config,
                split_seed=split_seed,
            ),
        )

    if source_config.kind == "current_trajectory":
        if current_trajectory is None:
            raise ValueError("current_trajectory eval source requires current_trajectory")
        trajectory_items = list(current_trajectory)
        trace_pack = build_trace_pack(
            trajectory_items,
            source_kind="current_trajectory",
            task_id=task_id,
        )
        context_snapshot = context_snapshot_for_current_trajectory(
            trajectory_items,
            task_id=trace_pack.task_id,
        )
        cases = (
            EvalCase(
                case_id=trace_pack.task_id,
                input=_trace_pack_input(trace_pack),
                metadata=_baseline_trajectory_set_metadata(
                    set_id=f"current_trajectory:{trace_pack.task_id}",
                    trace_pack=trace_pack,
                ),
                trace_pack=trace_pack,
                context_snapshot=context_snapshot,
                source={
                    "kind": "current_trajectory",
                    "task_id": trace_pack.task_id,
                    "set_id": f"current_trajectory:{trace_pack.task_id}",
                    "member_id": trace_pack.task_id,
                    "role": "baseline",
                },
            ),
        )
        cases = _filter_and_limit_cases(cases, source_config=source_config)
        return SelfEvolveDataset(
            cases=cases,
            recipe=build_dataset_recipe(
                cases,
                source_config=source_config,
                split_seed=split_seed,
            ),
        )

    if source_config.kind == "trajectory_log":
        if source_config.path is None:
            raise ValueError("trajectory_log eval source requires path")
        trajectory_path = Path(source_config.path).expanduser()
        records = load_trajectory_log_records(trajectory_path)
        packs = [
            build_trace_pack(
                record.trajectory,
                source_kind="trajectory_log",
                task_id=record.task_id,
            )
            for record in records
        ]
        snapshots = build_trajectory_context_snapshots(records)
        snapshots_by_task = {snapshot.case_id: snapshot for snapshot in snapshots}
        framework_packs = tuple(pack for pack in packs if is_framework_meta_trace_pack(pack))
        user_packs = tuple(pack for pack in packs if not is_framework_meta_trace_pack(pack))
        effective_packs = user_packs if framework_packs else tuple(packs)
        set_id = f"trajectory_log:{_file_fingerprint(trajectory_path)}"
        cases = _filter_and_limit_cases(
            (
                EvalCase(
                    case_id=pack.task_id,
                    input=input_with_reconstructed_context(
                        _trace_pack_input(pack),
                        snapshots_by_task[pack.task_id],
                    ),
                    metadata={
                        **_baseline_trajectory_set_metadata(
                            set_id=set_id,
                            trace_pack=pack,
                        ),
                        **(
                            {"framework_meta_trajectory": True}
                            if is_framework_meta_trace_pack(pack)
                            else {}
                        ),
                    },
                    trace_pack=pack,
                    context_snapshot=snapshots_by_task[pack.task_id],
                    source={
                        "kind": "trajectory_log",
                        "path": str(trajectory_path),
                        "task_id": pack.task_id,
                        "set_id": set_id,
                        "member_id": pack.task_id,
                        "role": "baseline",
                    },
                )
                for pack in effective_packs
            ),
            source_config=source_config,
        )
        dataset = SelfEvolveDataset(
            cases=cases,
            recipe=build_dataset_recipe(
                cases,
                source_config=source_config,
                split_seed=split_seed,
            ),
        )
        if framework_packs:
            source = dict(dataset.recipe.source)
            source["framework_meta_trajectory_filter"] = {
                "strategy": "exclude_framework_generated_from_user_baseline_set",
                "filtered_case_count": len(framework_packs),
                "filtered_case_ids": [pack.task_id for pack in framework_packs],
            }
            dataset = replace(dataset, recipe=replace(dataset.recipe, source=source))
        return dataset

    if source_config.kind == "trajectory_set":
        if source_config.path is None:
            raise ValueError("trajectory_set eval source requires path")
        cases = _filter_and_limit_cases(
            load_trajectory_set_eval_cases(Path(source_config.path).expanduser()),
            source_config=source_config,
        )
        return SelfEvolveDataset(
            cases=cases,
            recipe=build_dataset_recipe(
                cases,
                source_config=source_config,
                split_seed=split_seed,
            ),
        )

    if source_config.kind == "session":
        if source_config.path is None or source_config.session_id is None:
            raise ValueError("session eval source requires path and session_id")
        cases = _filter_and_limit_cases(
            load_session_eval_cases(
                Path(source_config.path).expanduser(),
                session_id=source_config.session_id,
            ),
            source_config=source_config,
        )
        return SelfEvolveDataset(
            cases=cases,
            recipe=build_dataset_recipe(
                cases,
                source_config=source_config,
                split_seed=split_seed,
            ),
        )

    if source_config.kind == "batch_config":
        if source_config.path is None:
            raise ValueError("batch_config eval source requires path")
        cases = _filter_and_limit_cases(
            load_batch_config_eval_cases(Path(source_config.path).expanduser()),
            source_config=source_config,
        )
        return SelfEvolveDataset(
            cases=cases,
            recipe=build_dataset_recipe(
                cases,
                source_config=source_config,
                split_seed=split_seed,
            ),
        )

    raise NotImplementedError(
        f"{source_config.kind} eval source is declared but not implemented in phase 1a"
    )


def load_batch_config_eval_cases(path: str | Path) -> list[EvalCase]:
    config_path = Path(path).expanduser()
    config = _load_config_mapping(config_path)
    dataset_value = config.get("eval_dataset_id_or_file_path")
    if not isinstance(dataset_value, str) or not dataset_value:
        raise ValueError("batch_config requires eval_dataset_id_or_file_path")
    dataset_path = Path(dataset_value)
    if not dataset_path.is_absolute():
        dataset_path = config_path.parent / dataset_path

    query_column = _string_or_none(config.get("eval_dataset_query_column")) or "query"
    answer_column = _string_or_none(config.get("eval_dataset_answer_column")) or "answer"

    cases: list[EvalCase] = []
    for line_number, raw_line in enumerate(
        dataset_path.read_text(encoding="utf-8").splitlines(),
        start=1,
    ):
        if not raw_line.strip():
            continue
        payload = json.loads(raw_line)
        if not isinstance(payload, Mapping):
            raise ValueError(f"batch dataset line {line_number} must be an object")
        case_id = _case_id(payload, resolved_path=dataset_path, line_number=line_number)
        cases.append(
            EvalCase(
                case_id=case_id,
                input=payload.get(query_column, payload.get("input")),
                expected_output=payload.get(answer_column),
                metadata=_without_keys(
                    payload,
                    {"case_id", "id", query_column, answer_column},
                ),
                source={
                    "kind": "batch_config",
                    "path": str(config_path),
                    "dataset_path": str(dataset_path),
                    "line_number": line_number,
                },
            )
        )
    return cases


def load_session_eval_cases(
    workspace_or_session_path: str | Path,
    *,
    session_id: str,
    max_cases: int | None = None,
) -> list[EvalCase]:
    session_path = _resolve_session_path(Path(workspace_or_session_path).expanduser(), session_id=session_id)
    payloads: list[tuple[int, Mapping[str, Any]]] = []
    for line_number, raw_line in enumerate(
        session_path.read_text(encoding="utf-8").splitlines(),
        start=1,
    ):
        if not raw_line.strip():
            continue
        payload = json.loads(raw_line)
        if not isinstance(payload, Mapping):
            raise ValueError(f"session log line {line_number} must be an object")
        payloads.append((line_number, payload))
    records = tuple(
        TrajectoryLogRecord(
            record_index=index,
            task_id=_session_case_id(
                payload,
                session_id=session_id,
                line_number=line_number,
            ),
            record_metadata={
                **dict(payload),
                "session_id": str(payload.get("session_id") or session_id),
            },
            trajectory=_trajectory_from_session_payload(
                payload,
                task_id=_session_case_id(
                    payload,
                    session_id=session_id,
                    line_number=line_number,
                ),
            ),
        )
        for index, (line_number, payload) in enumerate(payloads)
    )
    snapshots = build_trajectory_context_snapshots(records, source_kind="session")
    snapshots_by_task = {snapshot.case_id: snapshot for snapshot in snapshots}
    cases: list[EvalCase] = []
    for line_number, payload in payloads:
        if max_cases is not None and len(cases) >= max_cases:
            break
        case_id = _session_case_id(payload, session_id=session_id, line_number=line_number)
        trace_pack = _trace_pack_from_session_payload(payload, task_id=case_id)
        cases.append(
            EvalCase(
                case_id=case_id,
                input=payload.get("input") or {"task_id": case_id},
                expected_output=payload.get("final_answer"),
                metadata=_session_metadata(payload),
                trace_pack=trace_pack,
                context_snapshot=snapshots_by_task[case_id],
                source={
                    "kind": "session",
                    "path": str(session_path),
                    "session_id": session_id,
                    "line_number": line_number,
                },
            )
        )
    return cases


def load_trajectory_set_eval_cases(path: str | Path) -> list[EvalCase]:
    set_path = Path(path).expanduser().resolve()
    payload = json.loads(set_path.read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        raise ValueError("trajectory set must be a JSON object")
    schema_version = payload.get("schema_version")
    if schema_version != TRAJECTORY_SET_SCHEMA_VERSION:
        raise ValueError(
            "schema_version must be "
            f"{TRAJECTORY_SET_SCHEMA_VERSION!r}, got {schema_version!r}"
        )
    set_id = _required_string(payload, "set_id")
    target = _required_mapping(payload, "target")
    _required_string(target, "target_type", prefix="target.")
    _required_string(target, "target_id", prefix="target.")
    members_value = payload.get("members")
    if not isinstance(members_value, list):
        raise ValueError("members must be a list")
    if len(members_value) > TRAJECTORY_SET_MAX_MEMBERS:
        raise ValueError(
            f"members exceeds maximum of {TRAJECTORY_SET_MAX_MEMBERS}: "
            f"{len(members_value)}"
        )

    cases: list[EvalCase] = []
    seen_keys: set[tuple[Any, ...]] = set()
    for index, member_value in enumerate(members_value):
        if not isinstance(member_value, Mapping):
            raise ValueError(f"members[{index}] must be an object")
        member = member_value
        member_id = _required_string(member, "member_id", prefix=f"members[{index}].")
        role = _required_string(member, "role", prefix=f"members[{index}].")
        if role not in TRAJECTORY_SET_MEMBER_ROLES:
            raise ValueError(
                f"members[{index}].role must be one of "
                f"{sorted(TRAJECTORY_SET_MEMBER_ROLES)}, got {role!r}"
            )
        if role not in USER_AUTHORED_TRAJECTORY_SET_MEMBER_ROLES:
            raise ValueError(
                f"members[{index}].role {role} is framework-owned; "
                "use baseline/operator_added in user-authored trajectory-set files"
            )
        task_id = _required_string(member, "task_id", prefix=f"members[{index}].")
        task_input_digest = _required_string(
            member,
            "task_input_digest",
            prefix=f"members[{index}].",
        )
        duplicate_key = (
            task_input_digest,
            role,
            _string_or_none(member.get("candidate_id")),
            _string_or_none(member.get("source_run_id")),
        )
        if duplicate_key in seen_keys:
            raise ValueError(
                f"members[{index}] duplicates task_input_digest/role/"
                "candidate_id/source_run_id from an earlier member"
            )
        seen_keys.add(duplicate_key)
        trajectory_path = _resolve_trajectory_set_member_path(
            member.get("trajectory_path"),
            set_path=set_path,
            field=f"members[{index}].trajectory_path",
        )
        records = load_trajectory_log_records(trajectory_path)
        packs = [
            build_trace_pack(
                record.trajectory,
                source_kind="trajectory_log",
                task_id=record.task_id,
            )
            for record in records
        ]
        snapshots = build_trajectory_context_snapshots(
            records,
            source_kind="trajectory_set",
        )
        snapshots_by_task = {snapshot.case_id: snapshot for snapshot in snapshots}
        matching_pack = next((pack for pack in packs if pack.task_id == task_id), None)
        if matching_pack is None:
            raise ValueError(
                f"members[{index}].task_id {task_id!r} was not found in "
                f"{trajectory_path}"
            )
        source = {
            "kind": "trajectory_set",
            "path": str(set_path),
            "set_id": set_id,
            "member_id": member_id,
            "role": role,
            "trajectory_path": str(trajectory_path),
            "task_id": task_id,
            "task_input_digest": task_input_digest,
        }
        for key in (
            "source_run_id",
            "candidate_id",
            "evidence_bundle_path",
            "evaluator_report_path",
        ):
            value = _string_or_none(member.get(key))
            if value:
                source[key] = value
        cases.append(
            EvalCase(
                case_id=task_id,
                input=input_with_reconstructed_context(
                    _trace_pack_input(matching_pack),
                    snapshots_by_task[task_id],
                ),
                metadata={
                    "trajectory_set": {
                        "set_id": set_id,
                        "target": dict(target),
                        "member": dict(member),
                    }
                },
                trace_pack=matching_pack,
                context_snapshot=snapshots_by_task[task_id],
                source=source,
            )
        )
    return cases


def _filter_and_limit_cases(
    cases: Iterable[EvalCase],
    *,
    source_config: SelfEvolveEvalSourceConfig,
) -> tuple[EvalCase, ...]:
    selected_task_ids = set(source_config.task_ids)
    filtered = [
        case
        for case in cases
        if not selected_task_ids or case.case_id in selected_task_ids
    ]
    return tuple(filtered[: source_config.max_cases])


def build_dataset_recipe(
    cases: Iterable[EvalCase],
    *,
    source_config: SelfEvolveEvalSourceConfig,
    split_seed: str,
    synthetic_generation_policy: str = "disabled",
) -> DatasetRecipe:
    case_tuple = tuple(cases)
    splits = _split_case_ids(
        tuple(case.case_id for case in case_tuple),
        split_seed=split_seed,
    )
    return DatasetRecipe(
        source=_source_recipe(case_tuple, source_config=source_config),
        split_seed=split_seed,
        splits=splits,
        synthetic_generation_policy=synthetic_generation_policy,
        trainable_case_ids=tuple(splits["train"] + splits["validation"]),
        held_out_case_ids=tuple(splits["held_out"]),
    )


def _source_recipe(
    cases: tuple[EvalCase, ...],
    *,
    source_config: SelfEvolveEvalSourceConfig,
) -> Mapping[str, Any]:
    source: dict[str, Any] = {
        "kind": source_config.kind,
        "case_count": len(cases),
        "task_ids": list(source_config.task_ids),
        "fingerprint": _cases_fingerprint(cases),
    }
    if source_config.path is not None:
        path = Path(source_config.path).expanduser()
        source["path"] = str(path)
        if path.is_file():
            source["content_fingerprint"] = _file_fingerprint(path)
    if source_config.session_id is not None:
        source["session_id"] = source_config.session_id
    return source


def _baseline_trajectory_set_metadata(
    *,
    set_id: str,
    trace_pack: TracePack,
) -> Mapping[str, Any]:
    return {
        "trajectory_set": {
            "set_id": set_id,
            "target": None,
            "member": {
                "member_id": trace_pack.task_id,
                "role": "baseline",
                "task_id": trace_pack.task_id,
            },
        }
    }


def is_framework_meta_trace_pack(trace_pack: TracePack) -> bool:
    """Return true for framework/evaluator trajectories embedded in user logs.

    These traces are useful diagnostics, but they are not user task baselines. Keeping
    them in ordinary trajectory-log grouping can pollute target inference with
    evaluator/runtime-contract prompts.
    """
    haystack = "\n".join(_trace_pack_text_fragments(trace_pack)).lower()
    if not haystack:
        return False
    strong_markers = (
        "evaluation_runtime_contract",
        "artifact_backed_evidence",
        "do_not_call_external_tools",
        "report_output_path",
        "trajectory_log_path",
        "aworld_self_evolve_replay_artifact_dir",
        ".aworld/self_evolve/evaluator",
        ".aworld/self_evolve/cli-",
    )
    marker_count = sum(1 for marker in strong_markers if marker in haystack)
    if marker_count >= 1 and (
        "self-evolve" in haystack
        or "self_evolve" in haystack
        or "trajectory-evaluator" in haystack
        or "judge" in haystack
    ):
        return True
    return marker_count >= 2


def _trace_pack_text_fragments(trace_pack: TracePack) -> list[str]:
    fragments: list[str] = [trace_pack.pack_id, trace_pack.task_id]
    for step in trace_pack.steps:
        fragments.extend(_value_text_fragments(step.state))
        fragments.extend(_value_text_fragments(step.action))
        fragments.extend(_value_text_fragments(step.reward))
    return fragments


def _value_text_fragments(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, Mapping):
        fragments: list[str] = []
        for key, item in value.items():
            fragments.append(str(key))
            fragments.extend(_value_text_fragments(item))
        return fragments
    if isinstance(value, list):
        fragments = []
        for item in value:
            fragments.extend(_value_text_fragments(item))
        return fragments
    return []


def _split_case_ids(case_ids: tuple[str, ...], *, split_seed: str) -> Mapping[str, list[str]]:
    ordered = sorted(
        case_ids,
        key=lambda case_id: hashlib.sha256(
            f"{split_seed}:{case_id}".encode("utf-8")
        ).hexdigest(),
    )
    count = len(ordered)
    if count == 0:
        return {"train": [], "validation": [], "held_out": []}
    if count == 1:
        return {"train": ordered, "validation": [], "held_out": []}
    if count == 2:
        return {"train": ordered[:1], "validation": ordered[1:], "held_out": []}

    held_out_count = max(1, count // 5)
    validation_count = max(1, count // 5)
    train_count = count - validation_count - held_out_count
    return {
        "train": ordered[:train_count],
        "validation": ordered[train_count : train_count + validation_count],
        "held_out": ordered[train_count + validation_count :],
    }


def _cases_fingerprint(cases: tuple[EvalCase, ...]) -> str:
    payload = [
        {
            "case_id": case.case_id,
            "input": case.input,
            "expected_output": case.expected_output,
            "verification_command": case.verification_command,
            "metadata": case.metadata,
            "source": case.source,
            "context_snapshot_fingerprint": (
                case.context_snapshot.fingerprint
                if case.context_snapshot is not None
                else None
            ),
        }
        for case in cases
    ]
    return "sha256:" + hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()


def _file_fingerprint(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _load_config_mapping(path: Path) -> Mapping[str, Any]:
    raw_content = path.read_text(encoding="utf-8")
    if path.suffix.lower() == ".json":
        payload = json.loads(raw_content)
    else:
        import yaml

        payload = yaml.safe_load(raw_content)
    if not isinstance(payload, Mapping):
        raise ValueError(f"batch config must be an object: {path}")
    return payload


def _resolve_trajectory_set_member_path(
    value: Any,
    *,
    set_path: Path,
    field: str,
) -> Path:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field} is required")
    raw_path = Path(value).expanduser()
    resolved = (
        raw_path
        if raw_path.is_absolute()
        else set_path.parent / raw_path
    ).resolve()
    trusted_roots = (
        set_path.parent.resolve(),
        (set_path.parent / ".aworld" / "self_evolve").resolve(),
    )
    if not any(_is_relative_to(resolved, root) for root in trusted_roots):
        raise ValueError(f"{field} must resolve inside a trusted trajectory-set root")
    if not resolved.is_file():
        raise FileNotFoundError(f"{field} does not exist: {resolved}")
    return resolved


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _required_string(
    payload: Mapping[str, Any],
    key: str,
    *,
    prefix: str = "",
) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"{prefix}{key} is required")
    return value


def _required_mapping(payload: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    value = payload.get(key)
    if not isinstance(value, Mapping):
        raise ValueError(f"{key} is required")
    return value


def _case_id(payload: Mapping[str, Any], *, resolved_path: Path, line_number: int) -> str:
    for key in ("case_id", "id"):
        value = payload.get(key)
        if isinstance(value, str) and value:
            return value
    return f"{resolved_path.stem}:line-{line_number}"


def _trace_pack_input(trace_pack: TracePack) -> Any:
    if not trace_pack.steps:
        return {}
    state_input = trace_pack.steps[0].state.get("input")
    return state_input if state_input is not None else {}


def _resolve_session_path(workspace_or_session_path: str | Path, *, session_id: str) -> Path:
    path = Path(workspace_or_session_path)
    if path.is_file():
        return path
    return path / ".aworld" / "memory" / "sessions" / f"{_safe_session_id(session_id)}.jsonl"


def _safe_session_id(session_id: str) -> str:
    safe_id = "".join(
        character
        for character in session_id
        if character.isalnum() or character in {"-", "_"}
    ).strip()
    return safe_id or "default"


def _session_case_id(
    payload: Mapping[str, Any],
    *,
    session_id: str,
    line_number: int,
) -> str:
    task_id = payload.get("task_id")
    if isinstance(task_id, str) and task_id:
        return task_id
    return f"{session_id}:line-{line_number}"


def _session_metadata(payload: Mapping[str, Any]) -> Mapping[str, Any]:
    return {
        key: payload[key]
        for key in ("recorded_at", "task_status", "candidates", "llm_calls")
        if key in payload
    }


def _trace_pack_from_session_payload(
    payload: Mapping[str, Any],
    *,
    task_id: str,
) -> TracePack:
    return build_trace_pack(
        _trajectory_from_session_payload(payload, task_id=task_id),
        source_kind="session",
        task_id=task_id,
    )


def _trajectory_from_session_payload(
    payload: Mapping[str, Any],
    *,
    task_id: str,
) -> tuple[Mapping[str, Any], ...]:
    trajectory = payload.get("trajectory")
    if isinstance(trajectory, str):
        trajectory = json.loads(trajectory)
    if isinstance(trajectory, list):
        return tuple(
            item for item in trajectory if isinstance(item, Mapping)
        )

    return (
            {
                "meta": {
                    "step": 1,
                    "task_id": task_id,
                    "session_id": payload.get("session_id"),
                },
                "state": {"input": payload.get("input")},
                "action": {
                    "content": _string_or_none(payload.get("final_answer")),
                    "tool_calls": [],
                    "is_agent_finished": True,
                },
                "reward": {"status": payload.get("task_status")},
            },
    )


def _without_keys(payload: Mapping[str, Any], keys: set[str]) -> Mapping[str, Any]:
    return {key: value for key, value in payload.items() if key not in keys}


def _mapping_or_empty(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _string_or_none(value: Any) -> str | None:
    return value if isinstance(value, str) else None
