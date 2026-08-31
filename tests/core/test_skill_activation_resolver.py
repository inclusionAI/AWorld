import builtins
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "aworld-cli" / "src"))

from aworld_cli.core.skill_activation_resolver import (  # type: ignore[attr-defined]
    SkillActivationResolver,
    SkillResolverRequest,
)
from aworld.self_evolve.replay_capability import fingerprint_skill_package


def _write_skill(
    root: Path,
    skill_name: str,
    description: str | None = None,
    *,
    release_state: str | None = None,
) -> None:
    skill_dir = root / "skills" / skill_name
    skill_dir.mkdir(parents=True)
    self_evolve_block = (
        f"self_evolve:\n  release_state: {release_state}\n"
        if release_state is not None
        else ""
    )
    (skill_dir / "SKILL.md").write_text(
        (
            "---\n"
            f"name: {skill_name}\n"
            f"description: {description or skill_name}\n"
            f"{self_evolve_block}"
            "---\n\n"
            f"# {skill_name}\n"
        ),
        encoding="utf-8",
    )


def _write_manifest_skill_plugin(
    tmp_path: Path,
    *,
    plugin_id: str,
    skill_id: str,
    metadata: dict[str, object] | None = None,
    scope: str = "workspace",
    visibility: str = "public",
) -> Path:
    plugin_root = tmp_path / plugin_id
    manifest_dir = plugin_root / ".aworld-plugin"
    manifest_dir.mkdir(parents=True)
    _write_skill(plugin_root, skill_id)
    (manifest_dir / "plugin.json").write_text(
        json.dumps(
            {
                "id": plugin_id,
                "name": plugin_id,
                "version": "0.1.0",
                "entrypoints": {
                    "skills": [
                        {
                            "id": skill_id,
                            "name": skill_id,
                            "target": f"skills/{skill_id}/SKILL.md",
                            "scope": scope,
                            "visibility": visibility,
                            "metadata": metadata or {},
                        }
                    ]
                },
            }
        ),
        encoding="utf-8",
    )
    return plugin_root


def _append_manifest_skill(
    plugin_root: Path,
    skill_id: str,
    *,
    metadata: dict[str, object] | None = None,
    scope: str = "workspace",
    visibility: str = "public",
) -> None:
    _write_skill(plugin_root, skill_id)
    manifest_path = plugin_root / ".aworld-plugin" / "plugin.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest.setdefault("entrypoints", {}).setdefault("skills", []).append(
        {
            "id": skill_id,
            "name": skill_id,
            "target": f"skills/{skill_id}/SKILL.md",
            "scope": scope,
            "visibility": visibility,
            "metadata": metadata or {},
        }
    )
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")


def test_resolver_filters_by_scope_visibility_and_agent_selectors(tmp_path: Path) -> None:
    plugin_root = _write_manifest_skill_plugin(
        tmp_path,
        plugin_id="dev-tools",
        skill_id="browser-use",
        metadata={"agent_selectors": ["developer"]},
    )
    _append_manifest_skill(
        plugin_root,
        "session-only",
        scope="session",
        visibility="public",
    )
    _append_manifest_skill(
        plugin_root,
        "private-skill",
        scope="workspace",
        visibility="private",
    )

    result = SkillActivationResolver().resolve(
        SkillResolverRequest(
            plugin_roots=(plugin_root,),
            runtime_scope="workspace",
            agent_name="evaluator",
            task_text="use browser tools",
        )
    )

    assert "browser-use" not in result.skill_configs
    assert "session-only" not in result.skill_configs
    assert "private-skill" not in result.skill_configs


def test_resolver_explicit_request_beats_auto_match(tmp_path: Path) -> None:
    plugin_root = _write_manifest_skill_plugin(
        tmp_path,
        plugin_id="tools-pack",
        skill_id="browser-use",
        metadata={"match_keywords": ["browse", "browser"]},
    )
    _append_manifest_skill(
        plugin_root,
        "code-review",
        metadata={"match_keywords": ["review", "pull request"]},
    )

    result = SkillActivationResolver().resolve(
        SkillResolverRequest(
            plugin_roots=(plugin_root,),
            runtime_scope="workspace",
            agent_name="developer",
            task_text="review this PR in browser",
            requested_skill_names=("code-review",),
        )
    )

    assert result.active_skill_names == ("code-review",)
    assert result.skill_configs["code-review"]["active"] is True
    assert result.skill_configs["browser-use"]["active"] is False
    assert result.activation_evidence == (
        {
            "skill_name": "code-review",
            "canonical_skill_file": str(
                (plugin_root / "skills" / "code-review" / "SKILL.md").resolve()
            ),
            "canonical_skill_root": str(
                (plugin_root / "skills" / "code-review").resolve()
            ),
            "package_fingerprint": fingerprint_skill_package(
                plugin_root / "skills" / "code-review"
            ),
            "source": "aworld_cli_skill_activation_resolver",
        },
    )


def test_resolver_activation_attestation_does_not_import_self_evolve(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plugin_root = _write_manifest_skill_plugin(
        tmp_path,
        plugin_id="task-time-tools",
        skill_id="browser-use",
    )
    original_import = builtins.__import__

    def guarded_import(name: str, *args, **kwargs):
        if name == "aworld.self_evolve.replay_capability":
            raise AssertionError("task-time resolver imported self-evolve")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded_import)
    result = SkillActivationResolver().resolve(
        SkillResolverRequest(
            plugin_roots=(plugin_root,),
            runtime_scope="workspace",
            agent_name="developer",
            task_text="use browser-use",
            requested_skill_names=("browser-use",),
        )
    )

    assert result.active_skill_names == ("browser-use",)
    assert len(result.activation_evidence) == 1
    assert result.activation_evidence[0]["package_fingerprint"] == (
        fingerprint_skill_package(plugin_root / "skills" / "browser-use")
    )


def test_resolver_auto_match_is_deterministic(tmp_path: Path) -> None:
    plugin_root = _write_manifest_skill_plugin(
        tmp_path,
        plugin_id="alpha-tools",
        skill_id="browser-use",
        metadata={"match_keywords": ["browser"]},
    )
    _append_manifest_skill(
        plugin_root,
        "browser-debug",
        metadata={"match_keywords": ["browser"]},
    )

    result = SkillActivationResolver().resolve(
        SkillResolverRequest(
            plugin_roots=(plugin_root,),
            runtime_scope="workspace",
            agent_name="developer",
            task_text="browser browser browser",
        )
    )

    assert result.active_skill_names == ("browser-debug",)


def test_resolver_filters_disabled_skill_names(tmp_path: Path) -> None:
    plugin_root = _write_manifest_skill_plugin(
        tmp_path,
        plugin_id="media-tools",
        skill_id="youtube_search",
    )

    result = SkillActivationResolver().resolve(
        SkillResolverRequest(
            plugin_roots=(plugin_root,),
            runtime_scope="workspace",
            agent_name="developer",
            disabled_skill_names=("youtube_search",),
        )
    )

    assert "youtube_search" not in result.skill_configs


@pytest.mark.parametrize("blocked_state", ["draft", "candidate", "rejected", "disabled"])
def test_resolver_filters_unreleased_self_evolve_skill_candidates(
    tmp_path: Path,
    blocked_state: str,
) -> None:
    skills_root = tmp_path / "runtime-skills"
    _write_skill(skills_root, "media_comprehension", release_state=blocked_state)
    _write_skill(skills_root, "stable_skill", release_state="verified")
    _write_skill(skills_root, "legacy_skill")

    result = SkillActivationResolver().resolve(
        SkillResolverRequest(
            plugin_roots=tuple(),
            runtime_scope="workspace",
            agent_name="developer",
            compatibility_sources=(str(skills_root / "skills"),),
        )
    )

    assert "media_comprehension" not in result.skill_configs
    assert "stable_skill" in result.skill_configs
    assert "legacy_skill" in result.skill_configs


def test_resolver_isolated_candidate_overrides_ambient_same_name(
    tmp_path: Path,
) -> None:
    ambient_root = _write_manifest_skill_plugin(
        tmp_path,
        plugin_id="ambient-tools",
        skill_id="agent-browser",
    )
    candidate_root = tmp_path / "candidate-overlay"
    _write_skill(
        candidate_root,
        "agent-browser",
        description="candidate browser instructions",
        release_state="candidate",
    )

    result = SkillActivationResolver().resolve(
        SkillResolverRequest(
            plugin_roots=(ambient_root,),
            runtime_scope="session",
            agent_name="developer",
            requested_skill_names=("agent-browser",),
            compatibility_sources=(
                str(candidate_root / "skills" / "agent-browser"),
            ),
            isolated_candidate_sources=(
                str(candidate_root / "skills" / "agent-browser"),
            ),
        )
    )

    candidate_skill_root = candidate_root / "skills" / "agent-browser"
    assert result.active_skill_names == ("agent-browser",)
    assert result.skill_configs["agent-browser"]["description"] == (
        "candidate browser instructions"
    )
    assert result.activation_evidence == (
        {
            "skill_name": "agent-browser",
            "canonical_skill_file": str(
                (candidate_skill_root / "SKILL.md").resolve()
            ),
            "canonical_skill_root": str(candidate_skill_root.resolve()),
            "package_fingerprint": fingerprint_skill_package(
                candidate_skill_root
            ),
            "source": "aworld_cli_skill_activation_resolver",
        },
    )


def test_resolver_isolated_candidate_still_requires_explicit_request(
    tmp_path: Path,
) -> None:
    candidate_root = tmp_path / "candidate-overlay"
    _write_skill(
        candidate_root,
        "agent-browser",
        release_state="candidate",
    )

    result = SkillActivationResolver().resolve(
        SkillResolverRequest(
            plugin_roots=tuple(),
            runtime_scope="session",
            agent_name="developer",
            task_text="agent-browser",
            isolated_candidate_sources=(str(candidate_root / "skills"),),
        )
    )

    assert "agent-browser" not in result.skill_configs


def test_resolver_preserves_plugin_execution_entrypoint_metadata(tmp_path: Path) -> None:
    plugin_root = _write_manifest_skill_plugin(
        tmp_path,
        plugin_id="swarm-tools",
        skill_id="swarm",
        metadata={"entrypoint": "scripts/index.ts"},
    )
    scripts_dir = plugin_root / "skills" / "swarm" / "scripts"
    scripts_dir.mkdir(parents=True)
    (scripts_dir / "index.ts").write_text("export {};\n", encoding="utf-8")

    result = SkillActivationResolver().resolve(
        SkillResolverRequest(
            plugin_roots=(plugin_root,),
            runtime_scope="workspace",
            agent_name="developer",
            requested_skill_names=("swarm",),
        )
    )

    assert result.skill_configs["swarm"]["execution_assets"]["entrypoint"] == "scripts/index.ts"
