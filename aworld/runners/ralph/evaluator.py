# coding: utf-8
# Copyright (c) inclusionAI.
import json
from dataclasses import asdict, dataclass, field
from typing import TYPE_CHECKING, Any, Optional

from aworld.runners.ralph.config import RalphVerifyConfig

if TYPE_CHECKING:
    from aworld.core.task import Task, TaskResponse
    from aworld.runners.ralph.memory import LoopMemoryStore
    from aworld.runners.ralph.state import LoopContext


DEFAULT_VERIFY_TIMEOUT = 30


@dataclass
class VerifyCommandResult:
    command: str
    exit_code: int
    output: str
    passed: bool


@dataclass
class VerifyResult:
    passed: bool
    commands: list[VerifyCommandResult] = field(default_factory=list)
    success_policy: str = "all"

    def to_payload(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "success_policy": self.success_policy,
            "commands": [asdict(command) for command in self.commands],
        }


@dataclass
class IterationEvaluationResult:
    verify_result: Optional[VerifyResult] = None
    reflection_feedback: Optional[str] = None
    summary: Optional[str] = None


class IterationEvaluator:
    def __init__(
        self,
        context: "LoopContext",
        memory_store: "LoopMemoryStore",
        verify_config: RalphVerifyConfig,
    ):
        self.context = context
        self.memory_store = memory_store
        self.verify_config = verify_config

    async def evaluate(
        self,
        task: "Task",
        iter_num: int,
        execution_result: "TaskResponse",
        phase: str = "post_iteration",
    ) -> IterationEvaluationResult:
        summary = self._build_summary(execution_result)
        await self.memory_store.write_iteration_summary(task.id, iter_num, summary)

        if not self._should_run_verify(phase):
            return IterationEvaluationResult(summary=summary)

        verify_result = await self._run_verify()
        await self.memory_store.write_verify_result(task.id, iter_num, verify_result.to_payload())

        reflection_feedback = None
        if not verify_result.passed:
            reflection_feedback = self._build_verify_failure_feedback(verify_result)
            await self.memory_store.write_reflection_feedback(task.id, iter_num, reflection_feedback)

        return IterationEvaluationResult(
            verify_result=verify_result,
            reflection_feedback=reflection_feedback,
            summary=summary,
        )

    def _should_run_verify(self, phase: str) -> bool:
        if not (self.verify_config.enabled and self.verify_config.commands):
            return False

        if phase == "post_iteration":
            return bool(self.verify_config.run_on_each_iteration)
        if phase == "before_completion":
            return bool(self.verify_config.run_before_completion)
        return False

    async def _run_verify(self) -> VerifyResult:
        command_results: list[VerifyCommandResult] = []
        for command in self.verify_config.commands:
            terminal_result = await self.context.sand_box.terminal.run_code(
                code=command,
                timeout=getattr(self.verify_config, "timeout", DEFAULT_VERIFY_TIMEOUT),
                output_format="markdown",
            )
            command_results.append(self._parse_terminal_result(command, terminal_result))

        if self.verify_config.success_policy == "any":
            passed = any(result.passed for result in command_results)
        else:
            passed = all(result.passed for result in command_results)

        return VerifyResult(
            passed=passed,
            commands=command_results,
            success_policy=self.verify_config.success_policy,
        )

    def _parse_terminal_result(self, command: str, terminal_result: dict[str, Any]) -> VerifyCommandResult:
        terminal_data = self._decode_json_payload(terminal_result.get("data"))
        metadata = self._decode_json_payload(terminal_data.get("metadata"))
        exit_code = metadata.get("return_code")
        if exit_code is None:
            exit_code = 0 if terminal_data.get("success") else -1

        output = metadata.get("output_data")
        if output is None:
            output = terminal_data.get("message") or terminal_result.get("error") or ""
        output = str(output)
        output = output[: self.verify_config.max_output_chars]

        passed = exit_code == 0
        return VerifyCommandResult(
            command=command,
            exit_code=exit_code,
            output=output,
            passed=passed,
        )

    def _decode_json_payload(self, payload: Any) -> dict[str, Any]:
        if isinstance(payload, dict):
            return payload
        if isinstance(payload, str):
            try:
                decoded = json.loads(payload)
            except json.JSONDecodeError:
                return {"message": payload}
            if isinstance(decoded, dict):
                return decoded
            return {"message": decoded}
        if payload is None:
            return {}
        return {"message": payload}

    def _build_verify_failure_feedback(self, verify_result: VerifyResult) -> str:
        lines = [
            "Verification failed. Fix the issues below before continuing.",
            "",
        ]

        for command_result in verify_result.commands:
            if command_result.passed:
                continue
            lines.extend(
                [
                    f"Command: {command_result.command}",
                    f"Exit code: {command_result.exit_code}",
                    "Output:",
                    command_result.output or "(no output)",
                    "",
                ]
            )

        return "\n".join(lines).strip()

    def _build_summary(self, execution_result: "TaskResponse") -> str:
        if execution_result.answer is None:
            return str(execution_result.msg or "")

        if isinstance(execution_result.answer, str):
            return execution_result.answer

        try:
            return json.dumps(execution_result.answer, ensure_ascii=False)
        except TypeError:
            return str(execution_result.answer)
