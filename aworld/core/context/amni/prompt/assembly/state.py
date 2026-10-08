# coding: utf-8

from dataclasses import dataclass, field


@dataclass
class PromptAssemblyRuntimeState:
    seen_stable_prefix_hashes: set[str] = field(default_factory=set)
    seen_tool_fingerprints: set[str] = field(default_factory=set)
    last_stable_prefix_hash: str | None = None
    last_tool_fingerprint: str | None = None

    def mark_stable_prefix(self, stable_hash: str) -> bool:
        if not stable_hash:
            return False
        reused = stable_hash in self.seen_stable_prefix_hashes
        self.seen_stable_prefix_hashes.add(stable_hash)
        return reused

    def observe(
        self,
        *,
        stable_hash: str,
        tool_fingerprint: str,
    ) -> dict[str, bool]:
        """Track prefix and catalog continuity as independent dimensions."""

        prior_stable = self.last_stable_prefix_hash
        prior_tools = self.last_tool_fingerprint
        stable_reused = self.mark_stable_prefix(stable_hash)
        tool_reused = bool(
            tool_fingerprint
            and tool_fingerprint in self.seen_tool_fingerprints
        )
        if tool_fingerprint:
            self.seen_tool_fingerprints.add(tool_fingerprint)
        self.last_stable_prefix_hash = stable_hash or None
        self.last_tool_fingerprint = tool_fingerprint or None
        return {
            "stable_prefix_reused": stable_reused,
            "stable_prefix_changed": bool(
                prior_stable is not None and prior_stable != stable_hash
            ),
            "tool_catalog_reused": tool_reused,
            "tool_catalog_changed": bool(
                prior_tools is not None and prior_tools != tool_fingerprint
            ),
        }
