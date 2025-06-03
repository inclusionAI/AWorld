from abc import ABC, abstractmethod


class SandboxSetup(ABC):


    default_sandbox_timeout = 3000
    default_template = "base"

    @property
    @abstractmethod
    def sandbox_id(self) -> str:
        ...