from __future__ import annotations

import ast
import copy
from pathlib import Path

from aworld.core.context.compiler import (
    Authority,
    ContextKind,
    Lifetime,
    ScopeKind,
    SourceKind,
    Stability,
    Trust,
    thaw_json,
)
from aworld.core.context.compiler.adapters import OccurrenceContextAdapter
from aworld.skills.context_adapter import (
    SkillContentContextAdapter,
    SkillDescriptorContextAdapter,
    adapt_skill_contents,
    adapt_skill_descriptors,
)
from aworld.skills.models import SkillContent, SkillDescriptor


def _descriptor() -> SkillDescriptor:
    return SkillDescriptor(
        skill_id="filesystem:review-agent",
        provider_id="filesystem",
        skill_name="review-agent",
        display_name="Review Agent",
        description="Review a change",
        source_type="filesystem",
        scope="workspace",
        visibility="public",
        asset_root="/workspace/.agents/skills/review-agent",
        skill_file="/workspace/.agents/skills/review-agent/SKILL.md",
        metadata={"active": True, "nested": {"rank": 1}},
        execution_assets={"scripts": ["check.py"]},
        requirements={"eligible": True, "missing": []},
    )


def test_skill_descriptor_adapter_preserves_order_duplicates_and_owner_payload() -> None:
    duplicate = _descriptor()
    descriptors = [copy.deepcopy(duplicate), copy.deepcopy(duplicate)]
    before = copy.deepcopy(descriptors)

    adapter = SkillDescriptorContextAdapter()
    assert isinstance(adapter, OccurrenceContextAdapter)
    result = adapt_skill_descriptors(
        descriptors,
        source_identity="skills://workspace/registry",
        task_epoch=4,
    )

    assert descriptors == before
    assert [item.occurrence for item in result.items] == [0, 1]
    assert [item.kind for item in result.items] == [ContextKind.SKILL, ContextKind.SKILL]
    assert [item.source.kind for item in result.items] == [SourceKind.SKILL, SourceKind.SKILL]
    assert result.items[0].content_hash == result.items[1].content_hash
    assert result.items[0].id != result.items[1].id
    assert thaw_json(result.items[0].payload) == {
        "record_type": "descriptor",
        "skill_id": duplicate.skill_id,
        "provider_id": duplicate.provider_id,
        "skill_name": duplicate.skill_name,
        "display_name": duplicate.display_name,
        "description": duplicate.description,
        "source_type": duplicate.source_type,
        "scope": duplicate.scope,
        "visibility": duplicate.visibility,
        "asset_root": duplicate.asset_root,
        "skill_file": duplicate.skill_file,
        "metadata": dict(duplicate.metadata),
        "execution_assets": dict(duplicate.execution_assets),
        "requirements": dict(duplicate.requirements),
    }
    assert all(item.task_epoch == 4 for item in result.items)


def test_skill_content_adapter_preserves_loaded_content_without_claiming_activation() -> None:
    content = SkillContent(
        skill_id="filesystem:review-agent",
        usage="Use this only when explicitly selected.",
        tool_list={"read": {"required": True}},
        raw_frontmatter={"name": "review-agent", "description": "Review"},
        execution_assets={"references": ["contract.md"]},
    )
    before = copy.deepcopy(content)

    adapter = SkillContentContextAdapter()
    assert isinstance(adapter, OccurrenceContextAdapter)
    result = adapt_skill_contents(
        [content],
        source_identity="skills://workspace/content/review-agent",
    )
    item = result.items[0]

    assert content == before
    assert thaw_json(item.payload) == {
        "record_type": "content",
        "skill_id": content.skill_id,
        "usage": content.usage,
        "tool_list": dict(content.tool_list),
        "raw_frontmatter": dict(content.raw_frontmatter),
        "execution_assets": dict(content.execution_assets),
    }
    assert item.activation_reason == "observed_skill_content_occurrence"
    assert item.required is False
    assert "activation" in result.diagnostics[1].unknown_fields


def test_skill_owner_fields_do_not_infer_compiler_policy() -> None:
    result = adapt_skill_descriptors(
        [_descriptor()],
        source_identity="skills://workspace/registry",
    )
    item = result.items[0]

    assert item.authority is Authority.UNKNOWN
    assert item.scope.kinds == (ScopeKind.UNKNOWN,)
    assert item.lifetime is Lifetime.UNKNOWN
    assert item.trust is Trust.UNKNOWN
    assert item.stability is Stability.UNKNOWN
    assert item.task_epoch is None
    assert item.token_limit is None
    assert item.reducer is None
    assert set(result.diagnostics[1].unknown_fields) >= {
        "authority",
        "scope",
        "lifetime",
        "priority",
        "required",
        "trust",
        "stability",
        "token_estimate",
        "task_epoch",
        "activation",
    }


def test_compiler_core_does_not_import_skill_owner() -> None:
    compiler_root = Path(__file__).parents[2] / "aworld" / "core" / "context" / "compiler"
    imported_modules: set[str] = set()
    for module_path in compiler_root.glob("*.py"):
        tree = ast.parse(module_path.read_text(encoding="utf-8"))
        imported_modules.update(
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module is not None
        )
        imported_modules.update(
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        )

    assert not any(
        module == "aworld.skills" or module.startswith("aworld.skills.")
        for module in imported_modules
    )
