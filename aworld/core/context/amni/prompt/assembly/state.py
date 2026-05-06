# coding: utf-8

from dataclasses import dataclass, field


@dataclass
class PromptAssemblyRuntimeState:
    seen_stable_prefix_hashes: set[str] = field(default_factory=set)

    def mark_stable_prefix(self, stable_hash: str) -> bool:
        if not stable_hash:
            return False
        reused = stable_hash in self.seen_stable_prefix_hashes
        self.seen_stable_prefix_hashes.add(stable_hash)
        return reused
