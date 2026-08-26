"""Machine-readable AWorld Cloud HTTP commands."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import uuid
from pathlib import Path

from aworld_cli.cloud_client import CloudApiError, CloudClientConfig, CloudHttpClient


def _idempotency_key(value: str | None) -> str:
    return value or str(uuid.uuid4())


def _add_idempotency_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--idempotency-key",
        help="Stable retry key; a UUID is generated when omitted",
    )


class CloudTopLevelCommand:
    @property
    def name(self) -> str:
        return "cloud"

    @property
    def description(self) -> str:
        return "Use the AWorld Cloud Server API"

    @property
    def aliases(self) -> tuple[str, ...]:
        return ()

    def register_parser(self, subparsers) -> None:
        parser = subparsers.add_parser(
            self.name,
            help=self.description,
            description=self.description,
            prog="aworld-cli cloud",
        )
        parser.add_argument(
            "--endpoint",
            default=os.getenv("AWORLD_CLOUD_ENDPOINT", "http://localhost:8000"),
            help="Cloud Server URL (default: AWORLD_CLOUD_ENDPOINT or localhost:8000)",
        )
        parser.add_argument(
            "--token",
            default=os.getenv("AWORLD_CLOUD_TOKEN"),
            help="Bearer token (default: AWORLD_CLOUD_TOKEN)",
        )
        parser.add_argument(
            "--timeout",
            type=float,
            default=float(os.getenv("AWORLD_CLOUD_TIMEOUT", "30")),
            help="HTTP timeout in seconds (default: 30)",
        )
        resources = parser.add_subparsers(dest="cloud_resource", required=True)
        self._register_workspace_parser(resources)
        self._register_run_parser(resources)

    @staticmethod
    def _register_workspace_parser(resources) -> None:
        parser = resources.add_parser("workspace", help="Workspace operations")
        actions = parser.add_subparsers(dest="cloud_action", required=True)

        create = actions.add_parser("create", help="Create a workspace")
        create.add_argument("--name", required=True)
        create.add_argument("--profile", required=True)
        _add_idempotency_argument(create)

        list_parser = actions.add_parser("list", help="List workspaces")
        list_parser.add_argument("--limit", type=int, default=50)
        list_parser.add_argument("--page-token")

        show = actions.add_parser("show", help="Show a workspace")
        show.add_argument("workspace_id")

        release = actions.add_parser("release", help="Release a workspace")
        release.add_argument("workspace_id")
        _add_idempotency_argument(release)

    @staticmethod
    def _register_run_parser(resources) -> None:
        parser = resources.add_parser("run", help="Query and benchmark run operations")
        actions = parser.add_subparsers(dest="cloud_action", required=True)

        submit = actions.add_parser("submit", help="Submit a query or benchmark run")
        submit.add_argument("--workspace-id", required=True)
        submit.add_argument("--task", required=True)
        submit.add_argument("--model")
        submit.add_argument("--mode", choices=("query", "benchmark"), default="query")
        submit.add_argument("--dataset")
        submit.add_argument("--task-id")
        submit.add_argument("--harness")
        submit.add_argument("--verifier")
        _add_idempotency_argument(submit)

        list_parser = actions.add_parser("list", help="List runs")
        list_parser.add_argument("--workspace-id")
        list_parser.add_argument(
            "--state",
            choices=(
                "queued",
                "starting",
                "running",
                "cancelling",
                "succeeded",
                "failed",
                "cancelled",
            ),
        )
        list_parser.add_argument("--limit", type=int, default=50)
        list_parser.add_argument("--page-token")

        for action in ("show", "cancel", "retry", "events", "files"):
            action_parser = actions.add_parser(action, help=f"{action.title()} a run")
            action_parser.add_argument("run_id")
            if action in {"cancel", "retry"}:
                _add_idempotency_argument(action_parser)
            if action == "events":
                action_parser.add_argument("--after-sequence", type=int, default=0)
                action_parser.add_argument("--limit", type=int, default=100)

        wait = actions.add_parser("wait", help="Poll until a run is terminal")
        wait.add_argument("run_id")
        wait.add_argument("--poll-interval", type=float, default=2.0)
        wait.add_argument("--wait-timeout", type=float, default=3600.0)

        logs = actions.add_parser(
            "logs",
            help="Download stdout, stderr, and result files",
        )
        logs.add_argument("run_id")
        logs.add_argument("--output-dir", required=True)

        trajectory = actions.add_parser(
            "trajectory",
            help="Download the run's canonical ATIF trajectory",
        )
        trajectory.add_argument("run_id")
        trajectory.add_argument("--output", required=True)

    def run(self, args, context) -> int:
        del context
        try:
            config = CloudClientConfig(
                endpoint=args.endpoint,
                token=args.token,
                timeout_seconds=args.timeout,
            )
            payload = asyncio.run(self._execute(args, config))
        except (CloudApiError, ValueError, OSError) as exc:
            if isinstance(exc, CloudApiError):
                error = exc.as_dict()
            else:
                error = {
                    "error": {
                        "code": "invalid_cli_request",
                        "message": str(exc),
                        "details": {},
                        "status_code": None,
                    }
                }
            sys.stderr.write(
                json.dumps(error, ensure_ascii=False, sort_keys=True) + "\n"
            )
            return 1
        if payload is not None:
            sys.stdout.write(
                json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n"
            )
        return 0

    async def _execute(
        self,
        args,
        config: CloudClientConfig,
    ) -> dict[str, object] | None:
        async with CloudHttpClient(config) as client:
            if args.cloud_resource == "workspace":
                return await self._execute_workspace(client, args)
            return await self._execute_run(client, args)

    @staticmethod
    async def _execute_workspace(client: CloudHttpClient, args) -> dict[str, object]:
        if args.cloud_action == "create":
            return await client.create_workspace(
                name=args.name,
                profile_name=args.profile,
                idempotency_key=_idempotency_key(args.idempotency_key),
            )
        if args.cloud_action == "list":
            return await client.list_workspaces(
                limit=args.limit,
                page_token=args.page_token,
            )
        if args.cloud_action == "show":
            return await client.get_workspace(args.workspace_id)
        return await client.release_workspace(
            args.workspace_id,
            idempotency_key=_idempotency_key(args.idempotency_key),
        )

    @staticmethod
    async def _execute_run(
        client: CloudHttpClient,
        args,
    ) -> dict[str, object] | None:
        if args.cloud_action == "submit":
            benchmark = None
            if args.mode == "benchmark":
                if not args.dataset or not args.task_id:
                    raise ValueError("benchmark mode requires --dataset and --task-id")
                benchmark = {
                    "dataset": args.dataset,
                    "task_id": args.task_id,
                    "harness": args.harness,
                    "verifier": args.verifier,
                }
            elif any((args.dataset, args.task_id, args.harness, args.verifier)):
                raise ValueError("benchmark options require --mode benchmark")
            return await client.submit_run(
                args.workspace_id,
                task=args.task,
                model=args.model,
                mode=args.mode,
                benchmark=benchmark,
                idempotency_key=_idempotency_key(args.idempotency_key),
            )
        if args.cloud_action == "list":
            return await client.list_runs(
                limit=args.limit,
                page_token=args.page_token,
                workspace_id=args.workspace_id,
                state=args.state,
            )
        if args.cloud_action == "show":
            return await client.get_run(args.run_id)
        if args.cloud_action == "cancel":
            return await client.cancel_run(
                args.run_id,
                idempotency_key=_idempotency_key(args.idempotency_key),
            )
        if args.cloud_action == "retry":
            return await client.retry_run(
                args.run_id,
                idempotency_key=_idempotency_key(args.idempotency_key),
            )
        if args.cloud_action == "events":
            return await client.list_events(
                args.run_id,
                after_sequence=args.after_sequence,
                limit=args.limit,
            )
        if args.cloud_action == "files":
            return await client.list_files(args.run_id)
        if args.cloud_action == "wait":
            if args.poll_interval <= 0 or args.wait_timeout <= 0:
                raise ValueError("poll interval and wait timeout must be positive")
            loop = asyncio.get_running_loop()
            deadline = loop.time() + args.wait_timeout
            while True:
                run = await client.get_run(args.run_id)
                if run.get("state") in {
                    "succeeded",
                    "failed",
                    "cancelled",
                }:
                    return run
                if loop.time() >= deadline:
                    raise ValueError("timed out waiting for run to finish")
                await asyncio.sleep(args.poll_interval)
        if args.cloud_action == "logs":
            listing = await client.list_files(args.run_id)
            items = listing.get("items")
            if not isinstance(items, list):
                raise ValueError("server returned an invalid file listing")
            output_directory = Path(args.output_dir)
            output_directory.mkdir(parents=True, exist_ok=True)
            downloaded: list[dict[str, object]] = []
            for item in items:
                if not isinstance(item, dict) or item.get("kind") not in {
                    "stdout",
                    "stderr",
                    "result",
                }:
                    continue
                file_id = item.get("id")
                relative_path = item.get("relative_path")
                if not isinstance(file_id, str) or not isinstance(relative_path, str):
                    continue
                destination = output_directory / Path(relative_path).name
                content = await client.download_file(args.run_id, file_id)
                destination.write_bytes(content)
                downloaded.append(
                    {
                        "file_id": file_id,
                        "kind": item["kind"],
                        "output": str(destination),
                        "size_bytes": len(content),
                    }
                )
            return {"files": downloaded, "run_id": args.run_id}
        run = await client.get_run(args.run_id)
        file_id = run.get("canonical_trajectory_file_id")
        if not isinstance(file_id, str) or not file_id:
            raise ValueError("run does not have a canonical ATIF trajectory")
        content = await client.download_file(args.run_id, file_id)
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(content)
        return {
            "file_id": file_id,
            "format": "atif",
            "output": str(output),
            "run_id": args.run_id,
            "size_bytes": len(content),
        }
