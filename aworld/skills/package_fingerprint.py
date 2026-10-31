from __future__ import annotations

from pathlib import Path

from aworld.core.agent.skill import (
    SkillPackageValidationError,
    fingerprint_skill_package as _fingerprint_skill_package,
)


class SkillPackageFingerprintError(RuntimeError):
    """Raised when a skill package cannot form an immutable identity."""


def fingerprint_skill_package(skill_root: str | Path) -> str:
    """Fingerprint a complete skill package without importing self-evolve.

    This module deliberately depends only on the standard library so the CLI's
    task-time skill resolver can attest activation while the wider ``aworld``
    runtime is still being initialized.
    """

    try:
        return _fingerprint_skill_package(skill_root)
    except (OSError, SkillPackageValidationError, ValueError) as exc:
        raise SkillPackageFingerprintError(str(exc)) from exc
