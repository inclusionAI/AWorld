"""Leaf runtime verification and registry services for skill application."""

from __future__ import annotations

import hashlib
from pathlib import Path
from collections.abc import Callable, Mapping
from typing import Any

from aworld.self_evolve.candidate_package import candidate_package_reference_report
from aworld.self_evolve.target_package import _target_runtime_skill_path
from aworld.self_evolve.targets import SelfEvolveTarget
from aworld.self_evolve.types import CandidateVariant, EvaluationSummary
from aworld.skills.compat_provider import build_compat_registry


def _content_fingerprint(content: str) -> str:
    return "sha256:" + hashlib.sha256(content.encode("utf-8")).hexdigest()


def default_post_apply_evaluator(
    target: SelfEvolveTarget,
) -> Callable[[CandidateVariant], EvaluationSummary]:
    def evaluate(candidate: CandidateVariant) -> EvaluationSummary:
        target_path = _target_runtime_skill_path(target)
        loaded_skill_path: str | None = None
        runtime_skill_found = False
        loaded_from_real_path = False
        runtime_content_matches = False
        content_matches_target_file = (
            target_path.read_text(encoding="utf-8") == candidate.content
            if target_path is not None and target_path.exists()
            else False
        )
        package_references = candidate_package_reference_report(
            candidate,
            package_root=(target_path.parent if target_path is not None else None),
        )
        if target_path is not None:
            registry = build_compat_registry(target_path.parent.parent)
            descriptor = next(
                (
                    item
                    for item in registry.list_descriptors()
                    if item.skill_name == target.identity.target_id
                ),
                None,
            )
            if descriptor is not None:
                runtime_skill_found = True
                loaded_skill_path = descriptor.skill_file
                loaded_from_real_path = Path(descriptor.skill_file).resolve() == target_path
                loaded_content = Path(descriptor.skill_file).read_text(encoding="utf-8")
                runtime_content_matches = _content_fingerprint(
                    loaded_content
                ) == _content_fingerprint(candidate.content)
        post_apply_passed = (
            content_matches_target_file
            and runtime_skill_found
            and loaded_from_real_path
            and runtime_content_matches
            and package_references["closed"]
        )
        return EvaluationSummary(
            variant_id=candidate.candidate_id,
            dataset_split="post_apply",
            metrics={
                "post_apply_passed": post_apply_passed,
                "deterministic_signal": True,
                "evaluator_mode": "post_apply_runtime_loader",
                "content_matches_target_file": content_matches_target_file,
                "runtime_skill_found": runtime_skill_found,
                "loaded_from_real_path": loaded_from_real_path,
                "runtime_content_matches": runtime_content_matches,
                "candidate_package_references": package_references,
                "loaded_skill_path": loaded_skill_path,
                "expected_skill_path": str(target_path) if target_path is not None else None,
            },
        )

    return evaluate


def default_new_skill_registry_refresher(
    target: SelfEvolveTarget,
) -> Callable[[CandidateVariant], Mapping[str, Any]]:
    def refresh(candidate: CandidateVariant) -> Mapping[str, Any]:
        target_path = _target_runtime_skill_path(target)
        if target_path is None or not target_path.is_file():
            raise ValueError("published skill is unavailable for registry refresh")
        registry = build_compat_registry(target_path.parent.parent)
        descriptor = next(
            (
                item
                for item in registry.list_descriptors()
                if item.skill_name == candidate.target.target_id
            ),
            None,
        )
        if descriptor is None:
            raise ValueError("published skill is absent from refreshed registry")
        if Path(descriptor.skill_file).resolve() != target_path.resolve():
            raise ValueError("refreshed registry resolved the published skill elsewhere")
        return {
            "refreshed": True,
            "strategy": "compat_registry_rebuild",
            "skill_id": candidate.target.target_id,
        }

    return refresh


def default_new_skill_registry_compensator(
    target: SelfEvolveTarget,
) -> Callable[[CandidateVariant, object | None], Mapping[str, Any]]:
    """Reload the restored on-disk package after registry publication began."""

    restore = default_new_skill_registry_refresher(target)

    def compensate(
        candidate: CandidateVariant,
        _effect_result: object | None,
    ) -> Mapping[str, Any]:
        result = dict(restore(candidate))
        result["compensated"] = True
        result["strategy"] = "compat_registry_restore_after_rollback"
        return result

    return compensate
