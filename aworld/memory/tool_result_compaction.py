import json
from dataclasses import dataclass
from typing import Any, Sequence

from aworld.models.utils import num_tokens_from_string


def serialize_tool_result_content(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, (dict, list)):
        return json.dumps(content, ensure_ascii=False)
    return str(content)


def _build_preview(text: str, preview_chars: int) -> str:
    if preview_chars <= 0 or len(text) <= preview_chars:
        return text

    head_chars = max(preview_chars // 2, 1)
    tail_chars = max(preview_chars - head_chars, 1)
    return f"{text[:head_chars]}\n...\n{text[-tail_chars:]}"


@dataclass(frozen=True)
class ToolResultCompactionResult:
    content: Any
    applied: bool
    metadata: dict


def compact_tool_result_for_memory(
    content: Any,
    *,
    tool_name: str | None = None,
    action_name: str | None = None,
    summary_content: str | None = None,
    enabled: bool = True,
    tool_action_white_list: Sequence[str] | None = None,
    token_threshold: int = 30000,
    preview_chars: int = 2000,
    force: bool = False,
) -> ToolResultCompactionResult:
    serialized_content = serialize_tool_result_content(content)
    token_count = num_tokens_from_string(serialized_content) if serialized_content else 0
    tool_action_key = f"{tool_name}:{action_name}" if tool_name or action_name else None
    white_list = list(tool_action_white_list or [])

    trigger = None
    if enabled:
        if force:
            trigger = "metadata"
        elif tool_action_key and tool_action_key in white_list:
            trigger = "whitelist"
        elif token_count > max(token_threshold or 0, 0):
            trigger = "threshold"

    if not trigger:
        return ToolResultCompactionResult(
            content=content,
            applied=False,
            metadata={
                "applied": False,
                "original_token_count": token_count,
                "original_char_length": len(serialized_content),
            },
        )

    summary = summary_content.strip() if isinstance(summary_content, str) else None
    preview = _build_preview(serialized_content, max(preview_chars or 0, 0))

    prompt_lines = ["Tool output compacted for context reuse."]
    if tool_name or action_name:
        prompt_lines.append(
            f"Tool: {tool_name or 'unknown'} | Action: {action_name or 'unknown'}"
        )
    prompt_lines.append(
        f"Original size: {len(serialized_content)} chars, {token_count} tokens."
    )
    if summary:
        prompt_lines.append(f"Summary: {summary}")
    if preview:
        prompt_lines.append("Preview:")
        prompt_lines.append(preview)

    return ToolResultCompactionResult(
        content="\n".join(prompt_lines),
        applied=True,
        metadata={
            "applied": True,
            "trigger": trigger,
            "original_content": serialized_content,
            "original_token_count": token_count,
            "original_char_length": len(serialized_content),
            "summary_content": summary,
            "preview_chars": max(preview_chars or 0, 0),
        },
    )
