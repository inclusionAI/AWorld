"""Shared construction rules for AWorld's model-selected collaborators."""

from __future__ import annotations

import os
from collections.abc import Collection, Mapping, Sequence
from typing import Any

from aworld.agents.llm_agent import Agent
from aworld.config import AgentConfig, ModelConfig
from aworld.core.tool.tool_desc import is_tool_by_name


PUBLIC_COLLABORATOR_BOUNDARY = """
## Public collaboration boundary

Work only from the public task delegated by the root agent, the shared task
workspace, and evidence you directly observe with available tools. You do not
receive or infer a hidden grader, hidden reward, private rubric, or expected
benchmark answer. Your result is advisory public self-check evidence; it cannot
decide canonical reward or replace the root agent's completion decision.
""".strip()

READ_ONLY_FILESYSTEM_ACTIONS = frozenset(
    {
        "list_allowed_directories",
        "list_directory",
        "read_file",
        "read_media_file",
        "search_content",
        "search_files",
    }
)
READ_ONLY_FILESYSTEM_ALLOWLIST = {
    "filesystem": READ_ONLY_FILESYSTEM_ACTIONS,
}


def _mcp_tool_identity(
    schema: Mapping[str, Any],
    tool_mapping: Mapping[str, str],
) -> tuple[str, str] | None:
    """Resolve a presented MCP schema to its server and action names."""

    function = schema.get("function")
    if not isinstance(function, Mapping):
        return None
    presented_name = function.get("name")
    if not isinstance(presented_name, str):
        return None
    original_name = tool_mapping.get(presented_name, presented_name)
    if original_name.startswith("mcp__"):
        original_name = original_name[len("mcp__") :]
    if "__" not in original_name:
        return None
    server_name, action_name = original_name.split("__", 1)
    return server_name, action_name


def filter_mcp_tool_schemas_by_action_allowlist(
    schemas: Sequence[dict[str, Any]],
    *,
    tool_mapping: Mapping[str, str],
    action_allowlist: Mapping[str, Collection[str]],
) -> list[dict[str, Any]]:
    """Fail closed for actions on MCP servers governed by an allowlist.

    Schemas for local tools and MCP servers absent from ``action_allowlist`` are
    left unchanged. Every action on a governed server must be named explicitly,
    so newly added filesystem mutations are not exposed by default.
    """

    filtered: list[dict[str, Any]] = []
    for schema in schemas:
        identity = _mcp_tool_identity(schema, tool_mapping)
        if identity is None:
            filtered.append(schema)
            continue
        server_name, action_name = identity
        allowed_actions = action_allowlist.get(server_name)
        if allowed_actions is None or action_name in allowed_actions:
            filtered.append(schema)
    return filtered


class FreshAnswerOnlyCollaborator(Agent):
    """A shared-workspace collaborator with isolated reasoning state."""

    subagent_context_mode = "fresh"
    subagent_merge_mode = "answer_only"
    mcp_tool_action_allowlist: Mapping[str, Collection[str]] = {}

    def is_model_tool_call_allowed(self, full_name: str) -> bool:
        """Enforce the final collaborator schema at the execution boundary.

        Schema hiding is not a security boundary because a model can emit an
        explicit MCP identity. Accept friendly or canonical names only when
        they correspond to a function that survived the final live-schema
        allowlist.
        """
        if not isinstance(full_name, str) or not full_name:
            return False
        tool_mapping = getattr(self, "tool_mapping", {}) or {}
        allowed_names: set[str] = set()
        for schema in getattr(self, "tools", ()) or ():
            function = schema.get("function") if isinstance(schema, Mapping) else None
            presented_name = (
                function.get("name") if isinstance(function, Mapping) else None
            )
            if not isinstance(presented_name, str) or not presented_name:
                continue
            allowed_names.add(presented_name)
            original_name = tool_mapping.get(presented_name)
            if isinstance(original_name, str) and original_name:
                allowed_names.add(original_name)
                allowed_names.add(
                    original_name
                    if original_name.startswith("mcp__")
                    else f"mcp__{original_name}"
                )
        return full_name in allowed_names

    async def async_desc_transform(self, context) -> None:
        """Apply collaborator-specific MCP action policy to model schemas."""

        await super().async_desc_transform(context)
        if not self.mcp_tool_action_allowlist:
            return
        tool_mapping = getattr(self, "tool_mapping", {}) or {}
        self.tools = filter_mcp_tool_schemas_by_action_allowlist(
            self.tools,
            tool_mapping=tool_mapping,
            action_allowlist=self.mcp_tool_action_allowlist,
        )
        self.tool_mapping = {
            presented_name: original_name
            for presented_name, original_name in tool_mapping.items()
            if filter_mcp_tool_schemas_by_action_allowlist(
                [{"function": {"name": presented_name}}],
                tool_mapping={presented_name: original_name},
                action_allowlist=self.mcp_tool_action_allowlist,
            )
        }


def inherited_collaborator_config(
    parent: AgentConfig | None,
    *,
    resolver_inputs: Mapping[str, object] | None = None,
) -> AgentConfig:
    """Copy the active request profile without inheriting root task skills."""

    if parent is not None:
        config = parent.model_copy(deep=True)
    else:
        temperature = os.getenv("LLM_TEMPERATURE")
        config = AgentConfig(
            llm_config=ModelConfig(
                llm_model_name=os.getenv("LLM_MODEL_NAME"),
                llm_provider=os.getenv("LLM_PROVIDER"),
                llm_api_key=os.getenv("LLM_API_KEY"),
                llm_base_url=os.getenv("LLM_BASE_URL"),
                llm_temperature=float(temperature) if temperature else 0.1,
                llm_stream_call=(
                    os.getenv("STREAM", "0").lower() in {"1", "true", "yes"}
                ),
            ),
            skill_configs={},
        )

    # A collaborator receives only its explicit role prompt and delegated public
    # objective. Root Skills may contain solver-only workflow state and must not
    # leak into a fresh reviewer/developer context.
    config.skill_configs = {}
    config.ext = dict(config.ext or {})
    # Task-time resolution runs for every swarm member. Mark collaborators as
    # role-only so root default/requested skills cannot be reattached later and
    # contaminate a fresh verifier/evaluator/developer context.
    config.ext["skill_resolver_inputs"] = {"skills_disabled": True}
    return config


def registered_optional_tools(names: Sequence[str]) -> list[str]:
    """Return optional in-process tools that are actually registered."""

    return [name for name in names if is_tool_by_name(name)]


def public_collaborator_prompt(role_prompt: str) -> str:
    """Prepend the common public-only evidence boundary to a role prompt."""

    return f"{PUBLIC_COLLABORATOR_BOUNDARY}\n\n{role_prompt.strip()}\n"


__all__ = [
    "FreshAnswerOnlyCollaborator",
    "READ_ONLY_FILESYSTEM_ACTIONS",
    "READ_ONLY_FILESYSTEM_ALLOWLIST",
    "filter_mcp_tool_schemas_by_action_allowlist",
    "inherited_collaborator_config",
    "public_collaborator_prompt",
    "registered_optional_tools",
]
