import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "aworld-cli" / "src"))

from aworld_cli.core.skill_activation_resolver import (  # type: ignore[attr-defined]
    SkillActivationResolver,
    SkillResolverRequest,
)


def _write_skill(root: Path, skill_name: str, description: str | None = None) -> None:
    skill_dir = root / "skills" / skill_name
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        (
            "---\n"
            f"name: {skill_name}\n"
            f"description: {description or skill_name}\n"
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
