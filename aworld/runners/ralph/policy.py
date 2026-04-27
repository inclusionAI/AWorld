from dataclasses import dataclass

from aworld.runners.ralph.config import RalphConfig


@dataclass
class RalphLoopPolicy:
    execution_mode: str
    verify_enabled: bool

    @classmethod
    def from_config(cls, config: RalphConfig) -> "RalphLoopPolicy":
        return cls(
            execution_mode=config.execution_mode,
            verify_enabled=bool(config.verify.enabled),
        )
