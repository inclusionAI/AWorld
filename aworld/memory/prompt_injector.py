"""
Prompt injector for memory system.
Automatically injects memory-related instructions into agent system prompts.
"""

from typing import Set, List


class MemoryPromptInjector:
    """Injects memory-related prompts into agent system prompts."""

    def __init__(self, citations_mode: str = "on"):
        """Initialize prompt injector."""
        self.citations_mode = citations_mode

    def build_memory_section(self, available_tools: Set[str]) -> List[str]:
        """Build memory section for system prompt."""
        # Check if memory tools are available
        has_memory_search = "memory_search" in available_tools
        has_memory_get = "memory_get" in available_tools

        if not (has_memory_search or has_memory_get):
            return []

        lines = [
            "## Memory Recall",
            "Before answering anything about prior work, decisions, dates, people, preferences, or todos:",
            "run memory_search on MEMORY.md + memory/*.md; then use memory_get to pull only the needed lines.",
            "If low confidence after search, say you checked.",
        ]

        if self.citations_mode == "on":
            lines.append("")
            lines.append("Citations: include Source: <path#line> when it helps the user verify memory snippets.")

        lines.append("")  # Empty line at the end
        return lines

    def inject_into_prompt(self, system_prompt: str, available_tools: Set[str]) -> str:
        """Inject memory section into system prompt."""
        memory_section = self.build_memory_section(available_tools)
        
        if not memory_section:
            return system_prompt

        # Convert to string
        memory_text = "\n".join(memory_section)

        # Insert memory section after the main introduction
        # Look for common section markers
        markers = ["## Tools", "## Available Tools", "## Capabilities"]
        
        for marker in markers:
            if marker in system_prompt:
                # Insert before the marker
                parts = system_prompt.split(marker, 1)
                return parts[0] + memory_text + "\n" + marker + parts[1]

        # If no marker found, append to the end
        return system_prompt + "\n\n" + memory_text


def get_memory_prompt_injector(citations_mode: str = "on") -> MemoryPromptInjector:
    """Get a memory prompt injector instance."""
    return MemoryPromptInjector(citations_mode=citations_mode)
