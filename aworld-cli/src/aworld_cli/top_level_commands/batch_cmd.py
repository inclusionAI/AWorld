from __future__ import annotations

from aworld_cli.plugins.batch.cli import run_batch_command


class BatchTopLevelCommand:
    @property
    def name(self) -> str:
        return "batch-job"

    @property
    def description(self) -> str:
        return "Run batch jobs with agents using a YAML configuration file."

    @property
    def aliases(self) -> tuple[str, ...]:
        return ("batch",)

    def register_parser(self, subparsers) -> None:
        parser = subparsers.add_parser(
            "batch-job",
            help=self.description,
            description=self.description,
            prog="aworld-cli batch-job",
        )
        parser.add_argument(
            "config_path",
            type=str,
            help="Path to batch job YAML configuration file.",
        )
        parser.add_argument(
            "--remote-backend",
            type=str,
            help="Override remote backend defined in config file.",
        )

    def run(self, args, context) -> int | None:
        argv = [str(args.config_path)]
        if getattr(args, "remote_backend", None):
            argv.extend(["--remote-backend", str(args.remote_backend)])
        return run_batch_command(argv)
