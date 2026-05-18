# coding: utf-8

"""Shared guidance strings for workspace knowledge retrieval tools."""

KNOWLEDGE_TOOL_USAGE_LINES = (
    "Use list_knowledge_info(limit, offset) to discover artifact ids in the workspace when needed.",
    "Use get_knowledge_by_id(knowledge_id) to load a full artifact when the preview is insufficient.",
    "Use grep_knowledge(knowledge_id, pattern) to find the relevant section before loading more content.",
    "Use get_knowledge_by_lines(knowledge_id, start_line, end_line) to load only the lines you need.",
)

OFFLOAD_READBACK_NOTICE = (
    "This tool result was offloaded to workspace artifacts. "
    "The summaries below are compact previews for prompt efficiency. "
    + " ".join(KNOWLEDGE_TOOL_USAGE_LINES)
)


def build_knowledge_tool_tips() -> str:
    return "<tips>\n" + "\n".join(KNOWLEDGE_TOOL_USAGE_LINES) + "\n</tips>\n"
