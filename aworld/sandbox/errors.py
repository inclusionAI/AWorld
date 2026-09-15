# coding: utf-8
# Copyright (c) 2025 inclusionAI.

"""Typed failures raised by sandbox infrastructure."""


class SandboxInfrastructureError(RuntimeError):
    """A sandbox backend failure that task-level actions cannot repair."""

    failure_category = "infrastructure"

    def __init__(self, code: str, message: str):
        self.failure_code = code
        super().__init__(message)
