"""Benchmark-neutral paired evaluation contracts for Context improvements."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import math
import random
import re
from typing import Iterable

from aworld.core.context.compiler.frozen_json import (
    FrozenMap,
    canonical_json_hash,
    freeze_json,
)


_ALLOWED_VARIANT_FIELDS = {
    "agent_memory_config",
    "context_compiler",
    "docker_output_policy",
    "tool_output_policy",
    "artifact_offload",
    "progressive_skills",
    "progressive_tools",
    "completion_contract",
}
_ALLOWED_AGENT_MEMORY_FIELDS = {
    "history_scope",
    "enable_summary",
    "summary_rounds",
    "summary_context_length",
    "summary_summaried",
    "tool_result_offload",
    "tool_action_white_list",
    "tool_result_length_threshold",
    "tool_result_preview_chars",
}
_ALLOWED_DOCKER_OUTPUT_FIELDS = {
    "max_inline_output_bytes",
    "output_head_bytes",
}
_ALLOWED_COMPILER_FIELDS = {
    "mode",
    "compiler_version",
    "policy_version",
    "universal_final",
    "context_limit",
    "reserved_output_tokens",
    "provider_protocol_reserve",
    "safety_margin_tokens",
    "max_item_tokens",
    "require_proven_semantics_for_enforce",
    "scoped_instructions",
    "progressive_skills",
    "progressive_tools",
    "progressive_tool_base_tools",
    "task_catalog_policy",
    "checkpoint_policy",
    "default_tool_output_inline_tokens",
    "artifact_offload",
    "context_inspector",
    "trace_level",
    "completion_contract",
}
_ALLOWED_TOOL_OUTPUT_FIELDS = {
    "max_inline_tokens",
    "mode",
    "preserve_fields",
    "tail_tokens",
    "artifact_retention",
    "policy_version",
}


class TrialFidelity(str, Enum):
    COMPLETE = "complete"
    PARTIAL = "partial"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True, slots=True)
class ContextVariant:
    name: str
    settings: FrozenMap
    settings_hash: str

    @classmethod
    def build(cls, name: str, settings: dict) -> "ContextVariant":
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", name):
            raise ValueError("variant name must be stable")
        unknown = set(settings) - _ALLOWED_VARIANT_FIELDS
        if unknown:
            raise ValueError(
                "Context variants cannot change prompts, answers, tasks, or verifiers"
            )
        compiler = settings.get("context_compiler")
        if compiler is not None:
            if not isinstance(compiler, dict) or set(compiler) - _ALLOWED_COMPILER_FIELDS:
                raise ValueError("context_compiler variant contains non-Context fields")
        output_policy = settings.get("tool_output_policy")
        if output_policy is not None:
            if not isinstance(output_policy, dict) or set(output_policy) - _ALLOWED_TOOL_OUTPUT_FIELDS:
                raise ValueError("tool_output_policy variant contains unknown fields")
        agent_memory = settings.get("agent_memory_config")
        if agent_memory is not None and (
            not isinstance(agent_memory, dict)
            or set(agent_memory) - _ALLOWED_AGENT_MEMORY_FIELDS
        ):
            raise ValueError("agent_memory_config variant contains non-Context fields")
        docker_output = settings.get("docker_output_policy")
        if docker_output is not None and (
            not isinstance(docker_output, dict)
            or set(docker_output) - _ALLOWED_DOCKER_OUTPUT_FIELDS
        ):
            raise ValueError("docker_output_policy variant contains unknown fields")
        for field in ("artifact_offload", "progressive_skills", "progressive_tools"):
            if field in settings and not isinstance(settings[field], bool):
                raise TypeError(f"{field} must be a boolean")
        if "completion_contract" in settings and settings["completion_contract"] not in {
            "off", "observe", "enforce"
        }:
            raise ValueError("completion_contract variant must be a rollout mode")
        frozen = freeze_json(settings)
        if not isinstance(frozen, FrozenMap):
            raise TypeError("settings must be a JSON object")
        return cls(
            name=name,
            settings=frozen,
            settings_hash=canonical_json_hash(frozen),
        )


@dataclass(frozen=True, slots=True)
class ContextEvaluationManifest:
    experiment_id: str
    workload_id: str
    workload_kind: str
    dataset_checksum: str
    repository_snapshot: str
    environment_hash: str
    inference_profile_hash: str
    variants: tuple[ContextVariant, ...]
    case_ids: tuple[str, ...]
    repeats: int
    interleaving_seed: int
    independent_verifier_id: str
    manifest_hash: str
    cost_policy_hash: str | None = None

    @classmethod
    def build(
        cls,
        *,
        experiment_id: str,
        workload_id: str,
        workload_kind: str,
        dataset_checksum: str,
        repository_snapshot: str,
        environment_hash: str,
        inference_profile_hash: str,
        variants: Iterable[ContextVariant],
        case_ids: Iterable[str],
        repeats: int,
        interleaving_seed: int,
        independent_verifier_id: str,
        cost_policy_hash: str | None = None,
    ) -> "ContextEvaluationManifest":
        variant_values = tuple(variants)
        case_values = tuple(case_ids)
        for name, value in (
            ("experiment_id", experiment_id),
            ("workload_id", workload_id),
            ("workload_kind", workload_kind),
            ("independent_verifier_id", independent_verifier_id),
        ):
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must be non-empty")
        for name, value in (
            ("dataset_checksum", dataset_checksum),
            ("environment_hash", environment_hash),
            ("inference_profile_hash", inference_profile_hash),
        ):
            if not re.fullmatch(r"sha256:[0-9a-f]{64}", value):
                raise ValueError(f"{name} must be a canonical sha256 hash")
        if not isinstance(repository_snapshot, str) or not repository_snapshot.strip():
            raise ValueError("repository_snapshot must be non-empty")
        if cost_policy_hash is not None and not re.fullmatch(
            r"sha256:[0-9a-f]{64}", cost_policy_hash
        ):
            raise ValueError("cost_policy_hash must be canonical or None")
        if len(variant_values) < 2:
            raise ValueError("paired evaluation requires at least two variants")
        if len({variant.name for variant in variant_values}) != len(variant_values):
            raise ValueError("variant names must be unique")
        if len(set(case_values)) != len(case_values) or not case_values:
            raise ValueError("case ids must be non-empty and unique")
        if isinstance(repeats, bool) or not isinstance(repeats, int) or repeats <= 0:
            raise ValueError("repeats must be positive")
        payload = {
            "experiment_id": experiment_id,
            "workload_id": workload_id,
            "workload_kind": workload_kind,
            "dataset_checksum": dataset_checksum,
            "repository_snapshot": repository_snapshot,
            "environment_hash": environment_hash,
            "inference_profile_hash": inference_profile_hash,
            "variants": [
                {"name": variant.name, "settings_hash": variant.settings_hash}
                for variant in variant_values
            ],
            "case_ids": case_values,
            "repeats": repeats,
            "interleaving_seed": interleaving_seed,
            "independent_verifier_id": independent_verifier_id,
        }
        if cost_policy_hash is not None:
            payload["cost_policy_hash"] = cost_policy_hash
        return cls(
            variants=variant_values,
            case_ids=case_values,
            repeats=repeats,
            interleaving_seed=interleaving_seed,
            manifest_hash=canonical_json_hash(payload),
            cost_policy_hash=cost_policy_hash,
            **{
                key: payload[key]
                for key in (
                    "experiment_id",
                    "workload_id",
                    "workload_kind",
                    "dataset_checksum",
                    "repository_snapshot",
                    "environment_hash",
                    "inference_profile_hash",
                    "independent_verifier_id",
                )
            },
        )


@dataclass(frozen=True, slots=True)
class ContextTrialEvidence:
    manifest_hash: str
    case_id: str
    repeat: int
    variant: str
    request_hash: str
    trace_hash: str
    trajectory_checksum: str | None
    artifact_checksum: str | None
    verifier_result_hash: str
    reward: float
    fidelity: TrialFidelity
    metrics: FrozenMap

    def __post_init__(self) -> None:
        object.__setattr__(self, "fidelity", TrialFidelity(self.fidelity))
        metrics = freeze_json(self.metrics)
        if not isinstance(metrics, FrozenMap):
            raise TypeError("metrics must be a JSON object")
        object.__setattr__(self, "metrics", metrics)
        if isinstance(self.repeat, bool) or not isinstance(self.repeat, int) or self.repeat < 0:
            raise ValueError("repeat must be a non-negative integer")
        if not isinstance(self.reward, (int, float)) or isinstance(self.reward, bool):
            raise TypeError("reward must be numeric")
        if not math.isfinite(float(self.reward)):
            raise ValueError("reward must be finite")
        for name in ("manifest_hash", "request_hash", "trace_hash", "verifier_result_hash"):
            if not re.fullmatch(r"sha256:[0-9a-f]{64}", getattr(self, name)):
                raise ValueError(f"{name} must be a canonical sha256 hash")
        for name in ("trajectory_checksum", "artifact_checksum"):
            value = getattr(self, name)
            if value is not None and not re.fullmatch(r"sha256:[0-9a-f]{64}", value):
                raise ValueError(f"{name} must be canonical or None")
        if self.fidelity is TrialFidelity.COMPLETE and self.trajectory_checksum is None:
            raise ValueError("complete trial requires a trajectory checksum")


@dataclass(frozen=True, slots=True)
class PairedContextDelta:
    case_id: str
    repeat: int
    baseline_variant: str
    candidate_variant: str
    reward_delta: float
    metric_deltas: FrozenMap

    def __post_init__(self) -> None:
        if not math.isfinite(float(self.reward_delta)):
            raise ValueError("reward_delta must be finite")
        metrics = freeze_json(self.metric_deltas)
        if not isinstance(metrics, FrozenMap):
            raise TypeError("metric_deltas must be a JSON object")
        if any(
            not isinstance(value, (int, float))
            or isinstance(value, bool)
            or not math.isfinite(float(value))
            for value in metrics.values()
        ):
            raise ValueError("metric deltas must be finite numeric values")
        object.__setattr__(self, "metric_deltas", metrics)


@dataclass(frozen=True, slots=True)
class BootstrapInterval:
    lower: float
    upper: float
    confidence: float
    samples: int
    seed: int


@dataclass(frozen=True, slots=True)
class ContextBenefitSummary:
    complete_pairs: int
    mean_reward_delta: float
    reward_interval: BootstrapInterval
    metric_means: FrozenMap
    metric_intervals: FrozenMap


def build_paired_deltas(
    trials: Iterable[ContextTrialEvidence],
    *,
    baseline_variant: str,
    candidate_variant: str,
) -> tuple[PairedContextDelta, ...]:
    """Pair only complete trials from the same frozen manifest/case/repeat."""
    values = tuple(trials)
    complete = [trial for trial in values if trial.fidelity is TrialFidelity.COMPLETE]
    keys = [
        (trial.manifest_hash, trial.case_id, trial.repeat, trial.variant)
        for trial in complete
    ]
    if len(set(keys)) != len(keys):
        raise ValueError("complete trial evidence contains duplicate pair keys")
    manifest_hashes = {trial.manifest_hash for trial in complete}
    if len(manifest_hashes) > 1:
        raise ValueError("paired deltas require one frozen evaluation manifest")
    by_key = {(trial.case_id, trial.repeat, trial.variant): trial for trial in complete}
    deltas: list[PairedContextDelta] = []
    for case_id, repeat, variant in sorted(by_key):
        if variant != baseline_variant:
            continue
        baseline = by_key[(case_id, repeat, baseline_variant)]
        candidate = by_key.get((case_id, repeat, candidate_variant))
        if candidate is None or candidate.manifest_hash != baseline.manifest_hash:
            continue
        shared_metrics = set(baseline.metrics) & set(candidate.metrics)
        metric_deltas = {
            name: candidate.metrics[name] - baseline.metrics[name]
            for name in shared_metrics
            if isinstance(candidate.metrics[name], (int, float))
            and not isinstance(candidate.metrics[name], bool)
            and isinstance(baseline.metrics[name], (int, float))
            and not isinstance(baseline.metrics[name], bool)
        }
        deltas.append(
            PairedContextDelta(
                case_id=case_id,
                repeat=repeat,
                baseline_variant=baseline_variant,
                candidate_variant=candidate_variant,
                reward_delta=candidate.reward - baseline.reward,
                metric_deltas=freeze_json(metric_deltas),
            )
        )
    return tuple(deltas)


def _bootstrap_interval(
    values: tuple[float, ...],
    *,
    samples: int,
    confidence: float,
    seed: int,
) -> BootstrapInterval:
    if not values:
        raise ValueError("bootstrap requires at least one complete pair")
    if isinstance(samples, bool) or not isinstance(samples, int) or samples < 100:
        raise ValueError("bootstrap samples must be an integer >= 100")
    if not 0.0 < confidence < 1.0:
        raise ValueError("confidence must be between zero and one")
    rng = random.Random(seed)
    size = len(values)
    means = sorted(
        sum(values[rng.randrange(size)] for _ in range(size)) / size
        for _ in range(samples)
    )
    tail = (1.0 - confidence) / 2.0
    lower_index = max(0, min(samples - 1, int(tail * samples)))
    upper_index = max(
        0, min(samples - 1, int((1.0 - tail) * samples) - 1)
    )
    return BootstrapInterval(
        lower=means[lower_index],
        upper=means[upper_index],
        confidence=confidence,
        samples=samples,
        seed=seed,
    )


def summarize_context_benefit(
    deltas: Iterable[PairedContextDelta],
    *,
    bootstrap_samples: int = 2000,
    confidence: float = 0.95,
    seed: int = 0,
) -> ContextBenefitSummary:
    """Summarize complete paired evidence without benchmark-specific policy."""
    values = tuple(deltas)
    if not values:
        raise ValueError("benefit summary requires complete paired evidence")
    reward_values = tuple(float(value.reward_delta) for value in values)
    metric_names = sorted(
        set.intersection(*(set(value.metric_deltas) for value in values))
        if values
        else set()
    )
    metric_values: dict[str, tuple[float, ...]] = {}
    for name in metric_names:
        candidates = tuple(value.metric_deltas[name] for value in values)
        if all(
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and math.isfinite(float(value))
            for value in candidates
        ):
            metric_values[name] = tuple(float(value) for value in candidates)
    metric_intervals = {
        name: {
            "lower": interval.lower,
            "upper": interval.upper,
            "confidence": interval.confidence,
            "samples": interval.samples,
            "seed": interval.seed,
        }
        for index, (name, samples_for_metric) in enumerate(metric_values.items())
        for interval in (
            _bootstrap_interval(
                samples_for_metric,
                samples=bootstrap_samples,
                confidence=confidence,
                seed=seed + index + 1,
            ),
        )
    }
    return ContextBenefitSummary(
        complete_pairs=len(values),
        mean_reward_delta=sum(reward_values) / len(reward_values),
        reward_interval=_bootstrap_interval(
            reward_values,
            samples=bootstrap_samples,
            confidence=confidence,
            seed=seed,
        ),
        metric_means=freeze_json(
            {
                name: sum(samples_for_metric) / len(samples_for_metric)
                for name, samples_for_metric in metric_values.items()
            }
        ),
        metric_intervals=freeze_json(metric_intervals),
    )


__all__ = [
    "ContextEvaluationManifest",
    "ContextBenefitSummary",
    "ContextTrialEvidence",
    "ContextVariant",
    "PairedContextDelta",
    "BootstrapInterval",
    "TrialFidelity",
    "build_paired_deltas",
    "summarize_context_benefit",
]
