from __future__ import annotations

import os
from pathlib import Path

from aworld.benchmarks.parsebench.execution import (
    DEFAULT_PARSEBENCH_MODEL_PROFILE,
    DEFAULT_PARSEBENCH_TRAJECTORY_PATH,
    DEFAULT_PARSEBENCH_WORKSPACE_ROOT,
    ParseBenchExecutionError,
    run_parsebench_task,
)
from aworld.benchmarks.parsebench.verifier import (
    DEFAULT_GROUND_TRUTH_PATH,
    DEFAULT_LAYOUT_PATH,
    DEFAULT_MARKDOWN_PATH,
    DEFAULT_RESULT_PATH,
    DEFAULT_SCORER_CHECKOUT,
    DEFAULT_SCORER_PYTHON,
    DEFAULT_SCOPE_PATH,
    DEFAULT_VERIFIER_OUTPUT,
    ParseBenchVerificationError,
    verify_parsebench_task,
)
from aworld.benchmarks.parsebench.adapter import (
    DEFAULT_ARTIFACTS_ROOT,
    DEFAULT_PROVIDER,
    DEFAULT_TASK_SPEC_PATH,
)


class BenchmarkTopLevelCommand:
    @property
    def name(self) -> str:
        return "benchmark"

    @property
    def description(self) -> str:
        return "Run and verify built-in AWorld benchmark tasks."

    @property
    def aliases(self) -> tuple[str, ...]:
        return ()

    def register_parser(self, subparsers) -> None:
        parser = subparsers.add_parser(
            self.name,
            help=self.description,
            description=self.description,
            prog="aworld-cli benchmark",
        )
        benchmarks = parser.add_subparsers(
            dest="benchmark_name", required=True, title="benchmarks"
        )
        parsebench = benchmarks.add_parser(
            "parsebench",
            help="Run or verify a pinned llamaindex/ParseBench task.",
            description="Run or verify a pinned llamaindex/ParseBench task.",
        )
        actions = parsebench.add_subparsers(
            dest="benchmark_action", required=True, title="actions"
        )

        run = actions.add_parser(
            "run-task",
            help="Run FileX for one public ParseBench task.",
            description="Run FileX for one public ParseBench task.",
        )
        run.add_argument(
            "--task-spec",
            "--task",
            dest="task_spec",
            type=Path,
            default=DEFAULT_TASK_SPEC_PATH,
        )
        run.add_argument(
            "--workspace-root",
            type=Path,
            default=DEFAULT_PARSEBENCH_WORKSPACE_ROOT,
        )
        run.add_argument("--artifacts-root", type=Path, default=DEFAULT_ARTIFACTS_ROOT)
        run.add_argument(
            "--trajectory-output",
            "--trajectory",
            dest="trajectory_output",
            type=Path,
            default=DEFAULT_PARSEBENCH_TRAJECTORY_PATH,
        )
        run.add_argument(
            "--model-profile",
            default=os.environ.get(
                "FILEX_BENCHMARK_VLM_PROFILE",
                DEFAULT_PARSEBENCH_MODEL_PROFILE,
            ),
            help="Logical protected model profile (credentials stay in the environment).",
        )
        run.add_argument("--provider", default=DEFAULT_PROVIDER)
        run.add_argument("--filex-executable", default="filex")
        run.add_argument("--timeout", type=float, default=600.0)

        verify = actions.add_parser(
            "verify-task",
            help="Verify one FileX task with the pinned official scorer.",
            description="Verify one FileX task with the pinned official scorer.",
        )
        verify.add_argument(
            "--ground-truth", type=Path, default=DEFAULT_GROUND_TRUTH_PATH
        )
        verify.add_argument("--scope", type=Path, default=DEFAULT_SCOPE_PATH)
        verify.add_argument("--result", type=Path, default=DEFAULT_RESULT_PATH)
        verify.add_argument("--markdown", type=Path, default=DEFAULT_MARKDOWN_PATH)
        verify.add_argument("--layout", type=Path, default=DEFAULT_LAYOUT_PATH)
        verify.add_argument(
            "--verifier-output", type=Path, default=DEFAULT_VERIFIER_OUTPUT
        )
        verify.add_argument(
            "--scorer-checkout", type=Path, default=DEFAULT_SCORER_CHECKOUT
        )
        verify.add_argument("--scorer-python", type=Path, default=DEFAULT_SCORER_PYTHON)
        verify.add_argument("--scorer-timeout", type=float, default=8 * 60)

    def run(self, args, context) -> int | None:
        del context
        try:
            if args.benchmark_name != "parsebench":
                raise ParseBenchExecutionError(
                    "unsupported_benchmark", "unsupported benchmark"
                )
            if args.benchmark_action == "run-task":
                outcome = run_parsebench_task(
                    task_spec_path=args.task_spec,
                    workspace_root=args.workspace_root,
                    artifacts_root=args.artifacts_root,
                    trajectory_path=args.trajectory_output,
                    model_profile=args.model_profile,
                    provider=args.provider,
                    timeout_seconds=args.timeout,
                    filex_executable=args.filex_executable,
                )
                print(f"ParseBench task {outcome.task_id} artifacts emitted.")
                return 0
            if args.benchmark_action == "verify-task":
                outcome = verify_parsebench_task(
                    ground_truth_path=args.ground_truth,
                    scope_path=args.scope,
                    result_path=args.result,
                    markdown_path=args.markdown,
                    layout_path=args.layout,
                    verifier_output=args.verifier_output,
                    scorer_checkout=args.scorer_checkout,
                    scorer_python=args.scorer_python,
                    scorer_timeout_seconds=args.scorer_timeout,
                )
                print(outcome.stdout_line)
                # A non-publishable smoke/custom scope is still a successful
                # local verification. Publication gating belongs to the
                # versioned result/reducer, not the process exit status.
                return 0
            raise ParseBenchExecutionError(
                "unsupported_action", "unsupported ParseBench action"
            )
        except (ParseBenchExecutionError, ParseBenchVerificationError) as exc:
            print(f"ParseBench benchmark error [{exc.code}]: {exc}")
            return 2


__all__ = ("BenchmarkTopLevelCommand",)
