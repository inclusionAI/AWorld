from __future__ import annotations

import json
import math
import os
import secrets
from pathlib import Path

from aworld.benchmarks.parsebench.adapter import (
    DEFAULT_ARTIFACTS_ROOT,
    DEFAULT_PROVIDER,
    DEFAULT_TASK_SPEC_PATH,
)
from aworld.benchmarks.parsebench.contracts import (
    ParseBenchDatasetError,
    SmokeSelection,
)
from aworld.benchmarks.parsebench.dataset import (
    DEFAULT_SERVICE_NAME,
    build_parsebench_executable_dataset,
)
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
    DEFAULT_SCOPE_PATH,
    DEFAULT_SCORER_CHECKOUT,
    DEFAULT_SCORER_PYTHON,
    DEFAULT_VERIFIER_OUTPUT,
    DEFAULT_VERIFIER_WORKSPACE_ROOT,
    ParseBenchVerificationError,
    verify_parsebench_task,
)
from aworld_cli.parsebench_gateway import (
    DEFAULT_MODEL_PROFILE,
    GatewayRunManifest,
    ParseBenchGatewayClient,
    ParseBenchGatewayError,
    bind_image_build_intent,
    bind_submission_intent,
    build_submission_intent,
    build_submit_payload,
    finalize_json_output,
    inspect_parsebench_package,
    load_gateway_run_manifest,
    load_submission_intent,
    reduce_gateway_batch_results,
    release_json_reservation,
    reserve_json_output,
    select_package_tasks,
    validate_gateway_run_destination,
)

DEFAULT_GATEWAY_URL_ENV = "MCPGATEWAY_URL"
DEFAULT_GATEWAY_TOKEN_ENV = "MCPGATEWAY_JWT_TOKEN"
DEFAULT_GATEWAY_STAFF_ID_ENV = "MCPGATEWAY_STAFF_ID"
DEFAULT_RUN_MANIFEST_PATH = Path("parsebench-run.json")
DEFAULT_REPORT_PATH = Path("parsebench-report.json")


def _print_json(value: object) -> None:
    print(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    )


def _gateway_client(args) -> ParseBenchGatewayClient:
    gateway_url = _gateway_url(args)
    token = os.environ.get(args.token_env) if args.token_env else None
    staff_id = os.environ.get(args.staff_id_env) if args.staff_id_env else None
    return ParseBenchGatewayClient(
        gateway_url,
        token=token,
        staff_id=staff_id,
        timeout_seconds=args.gateway_timeout,
    )


def _gateway_url(args) -> str:
    gateway_url = args.gateway_url or os.environ.get(DEFAULT_GATEWAY_URL_ENV)
    if not gateway_url:
        raise ParseBenchGatewayError(
            "gateway_url_missing",
            f"set --gateway-url or {DEFAULT_GATEWAY_URL_ENV}",
        )
    return gateway_url


def _add_gateway_arguments(parser) -> None:
    parser.add_argument(
        "--gateway-url",
        help=f"mcpgateway base URL (or {DEFAULT_GATEWAY_URL_ENV}).",
    )
    parser.add_argument(
        "--token-env",
        default=DEFAULT_GATEWAY_TOKEN_ENV,
        help="Environment variable containing the bearer token; never put tokens in argv.",
    )
    parser.add_argument(
        "--staff-id-env",
        default=DEFAULT_GATEWAY_STAFF_ID_ENV,
        help="Optional environment variable containing the staff identity header.",
    )
    parser.add_argument("--gateway-timeout", type=float, default=120.0)


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

        prepare = actions.add_parser(
            "prepare",
            help="Build a pinned mcpgateway executable Dataset package.",
            description=(
                "Build a deterministic ParseBench executable Dataset package. "
                "The default is a one-per-dimension smoke selection."
            ),
        )
        prepare.add_argument("--source", required=True, type=Path)
        prepare.add_argument("--output", required=True, type=Path)
        prepare.add_argument("--dataset-id")
        prepare.add_argument("--service-name", default=DEFAULT_SERVICE_NAME)
        prepare.add_argument("--runtime-image", required=True)
        prepare.add_argument("--allow-mutable-local-image", action="store_true")
        selection = prepare.add_mutually_exclusive_group()
        selection.add_argument(
            "--smoke-per-dimension",
            type=int,
            help="Build a deterministic smoke package (default: 1).",
        )
        selection.add_argument(
            "--full",
            action="store_true",
            help="Build all 2,078 pinned executions; this does not run them.",
        )
        prepare.add_argument("--smoke-seed", default="parsebench-smoke-v1")

        submit = actions.add_parser(
            "submit",
            help="Import a package and submit selected tasks to mcpgateway + Arca.",
        )
        submit.add_argument("--package", required=True, type=Path)
        submit.add_argument(
            "--run-manifest",
            type=Path,
            default=DEFAULT_RUN_MANIFEST_PATH,
        )
        submit.add_argument("--task-id", action="append", default=[])
        submit.add_argument("--limit", type=int)
        submit.add_argument(
            "--full",
            action="store_true",
            help="Explicitly authorize submitting the complete official release.",
        )
        submit.add_argument(
            "--resume",
            action="store_true",
            help="Resume the exact saved submission intent after an interrupted request.",
        )
        submit.add_argument("--model-profile", default=DEFAULT_MODEL_PROFILE)
        submit.add_argument("--task-timeout", type=int, default=3_600)
        submit.add_argument(
            "--image-build-timeout",
            type=float,
            default=3_600.0,
            help="Seconds to wait for the selected publication's Task images.",
        )
        _add_gateway_arguments(submit)

        status = actions.add_parser(
            "status",
            help="Read bounded mcpgateway status for a submitted ParseBench run.",
        )
        status.add_argument(
            "--run-manifest",
            type=Path,
            default=DEFAULT_RUN_MANIFEST_PATH,
        )
        _add_gateway_arguments(status)

        report = actions.add_parser(
            "report",
            help="Fetch terminal rewards and write the deterministic ParseBench report.",
        )
        report.add_argument(
            "--run-manifest",
            type=Path,
            default=DEFAULT_RUN_MANIFEST_PATH,
        )
        report.add_argument("--output", type=Path, default=DEFAULT_REPORT_PATH)
        report.add_argument("--page-size", type=int, default=100)
        _add_gateway_arguments(report)

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
            "--workspace-root", type=Path, default=DEFAULT_VERIFIER_WORKSPACE_ROOT
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
            if args.benchmark_action == "prepare":
                smoke_selection = None
                if not args.full:
                    smoke_selection = SmokeSelection(
                        per_dimension=(
                            1
                            if args.smoke_per_dimension is None
                            else args.smoke_per_dimension
                        ),
                        seed=args.smoke_seed,
                    )
                result = build_parsebench_executable_dataset(
                    args.source,
                    args.output,
                    selection=smoke_selection,
                    dataset_id=args.dataset_id,
                    service_name=args.service_name,
                    runtime_image=args.runtime_image,
                    allow_mutable_local_image=args.allow_mutable_local_image,
                )
                _print_json(
                    {
                        "schema": "aworld.parsebench.prepare/v1",
                        "output": str(result.output),
                        "package_sha256": result.package_sha256,
                        "task_count": result.task_count,
                        "rule_count": result.rule_count,
                        "dimensions": [
                            dimension.value for dimension in result.dimensions
                        ],
                        "selection_kind": (
                            "official-full" if result.selection is None else "smoke"
                        ),
                        "selection_manifest_sha256": (result.selection_manifest_sha256),
                        "publishable": result.publishable,
                    }
                )
                return 0
            if args.benchmark_action == "submit":
                package = inspect_parsebench_package(args.package)
                selected_task_ids = select_package_tasks(
                    package,
                    task_ids=args.task_id,
                    limit=args.limit,
                    allow_full=args.full,
                )
                if (
                    not math.isfinite(args.image_build_timeout)
                    or not 60 <= args.image_build_timeout <= 14_400
                ):
                    raise ValueError(
                        "image_build_timeout must be between 60 and 14400 seconds"
                    )
                new_intent = build_submission_intent(
                    package,
                    selected_task_ids=selected_task_ids,
                    model_profile=args.model_profile,
                    timeout_seconds=args.task_timeout,
                    gateway_url=_gateway_url(args),
                )
                if args.resume:
                    intent, publication = load_submission_intent(
                        args.run_manifest,
                        package=package,
                        selected_task_ids=selected_task_ids,
                        model_profile=args.model_profile,
                        timeout_seconds=args.task_timeout,
                        gateway_url=_gateway_url(args),
                    )
                else:
                    intent = new_intent
                    publication = None
                with _gateway_client(args) as client:
                    if not args.resume:
                        reserve_json_output(args.run_manifest, intent)
                    if publication is None:
                        publication = client.import_package(
                            package,
                            client_request_id=intent["client_request_id"],
                        )
                        bound_intent = bind_submission_intent(intent, publication)
                        finalize_json_output(
                            args.run_manifest,
                            reservation=intent,
                            value=bound_intent,
                        )
                        intent = bound_intent
                    image_receipt = client.prepare_task_images(
                        package,
                        selected_task_ids=selected_task_ids,
                        publication=publication,
                        timeout_seconds=args.image_build_timeout,
                    )
                    if intent["status"] == "imported":
                        ready_intent = bind_image_build_intent(
                            intent,
                            image_receipt,
                        )
                        finalize_json_output(
                            args.run_manifest,
                            reservation=intent,
                            value=ready_intent,
                        )
                        intent = ready_intent
                    elif (
                        intent.get("status") != "images_ready"
                        or intent.get("image_build_receipt") != image_receipt.to_dict()
                    ):
                        raise ParseBenchGatewayError(
                            "submission_intent_conflict",
                            "ParseBench image readiness changed while resuming",
                        )
                    payload = build_submit_payload(
                        package,
                        selected_task_ids=selected_task_ids,
                        publication=publication,
                        client_request_id=intent["client_request_id"],
                        model_profile=args.model_profile,
                        timeout_seconds=args.task_timeout,
                    )
                    response = client.submit(payload)
                manifest = GatewayRunManifest.from_submission(
                    package,
                    selected_task_ids=selected_task_ids,
                    model_profile=args.model_profile,
                    publication=publication,
                    image_build=image_receipt,
                    gateway_url=_gateway_url(args),
                    client_request_id=intent["client_request_id"],
                    response=response,
                )
                finalize_json_output(
                    args.run_manifest,
                    reservation=intent,
                    value=manifest.to_dict(),
                )
                _print_json(
                    {
                        "schema": "aworld.parsebench.submit/v1",
                        "batch_id": manifest.batch_id,
                        "dataset_id": manifest.dataset_id,
                        "model_profile": manifest.model_profile,
                        "run_manifest": str(args.run_manifest.resolve()),
                        "task_count": len(manifest.task_to_run),
                    }
                )
                return 0
            if args.benchmark_action in {"status", "report"}:
                manifest = load_gateway_run_manifest(args.run_manifest)
                validate_gateway_run_destination(manifest, _gateway_url(args))
                if args.benchmark_action == "status":
                    with _gateway_client(args) as client:
                        results = client.batch_status(
                            manifest.batch_id,
                            expected_total=len(manifest.task_to_run),
                        )
                else:
                    if not 1 <= args.page_size <= 1_000:
                        raise ValueError("page_size must be between 1 and 1000")
                    reservation = {
                        "schema": "aworld.parsebench.gateway-report-reservation/v1",
                        "batch_id": manifest.batch_id,
                        "reservation_id": secrets.token_hex(16),
                    }
                    reserve_json_output(args.output, reservation)
                    try:
                        with _gateway_client(args) as client:
                            status = client.batch_status(
                                manifest.batch_id,
                                expected_total=len(manifest.task_to_run),
                            )
                            if status["status"] != "done":
                                raise ParseBenchGatewayError(
                                    "batch_not_terminal",
                                    "ParseBench batch is not terminal",
                                )
                            results = client.batch_results(
                                manifest.batch_id,
                                page_size=args.page_size,
                                expected_total=len(manifest.task_to_run),
                            )
                            if any(
                                results[key] != status[key]
                                for key in (
                                    "batch_id",
                                    "status",
                                    "total",
                                    "completed",
                                    "failed",
                                )
                            ):
                                raise ParseBenchGatewayError(
                                    "gateway_response_invalid",
                                    "ParseBench batch snapshot changed during report generation",
                                )
                        report_payload = reduce_gateway_batch_results(manifest, results)
                        finalize_json_output(
                            args.output,
                            reservation=reservation,
                            value=report_payload,
                        )
                    except BaseException:
                        release_json_reservation(args.output, reservation)
                        raise
                if args.benchmark_action == "status":
                    _print_json(
                        {
                            "schema": "aworld.parsebench.status/v1",
                            "batch_id": manifest.batch_id,
                            "status": results["status"],
                            "total": results["total"],
                            "completed": results["completed"],
                            "failed": results["failed"],
                            "terminal": results["status"] == "done",
                        }
                    )
                    return 0
                _print_json(
                    {
                        "schema": "aworld.parsebench.report-written/v1",
                        "batch_id": manifest.batch_id,
                        "output": str(args.output.resolve()),
                        "status": report_payload["status"],
                        "publishable": report_payload["publishable"],
                        "overall_score": report_payload["overall_score"],
                    }
                )
                return 0
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
                    workspace_root=args.workspace_root,
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
        except (
            ParseBenchDatasetError,
            ParseBenchExecutionError,
            ParseBenchGatewayError,
            ParseBenchVerificationError,
            ValueError,
        ) as exc:
            code = getattr(exc, "code", "invalid_arguments")
            print(f"ParseBench benchmark error [{code}]: {exc}")
            return 2


__all__ = ("BenchmarkTopLevelCommand",)
