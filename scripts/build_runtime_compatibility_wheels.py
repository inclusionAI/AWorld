#!/usr/bin/env python3
"""Build the Runtime compatibility wheels with a reproducible timestamp."""

from __future__ import annotations

import argparse
import ast
from email.parser import Parser
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import tomllib
from zipfile import ZipFile


ROOT = Path(__file__).resolve().parents[1]
CLI_MANIFEST = Path("packaging/runtime/aworld-cli.pyproject.toml")


def _literal_assignment(path: Path, name: str) -> str:
    module = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for statement in module.body:
        if isinstance(statement, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == name
            for target in statement.targets
        ):
            value = ast.literal_eval(statement.value)
            if isinstance(value, str):
                return value
    raise RuntimeError(f"{name} is not a string assignment in {path}")


def _source_versions(source: Path) -> tuple[str, str]:
    core = _literal_assignment(source / "aworld/version_gen.py", "__version__")
    cli_metadata = tomllib.loads((source / CLI_MANIFEST).read_text(encoding="utf-8"))
    cli = cli_metadata["project"]["version"]
    if f"aworld=={core}" not in cli_metadata["project"]["dependencies"]:
        raise RuntimeError("Runtime CLI manifest must pin the exact core version")
    return core, cli


def _source_date_epoch(raw: str | None) -> int:
    if raw is None or not raw.strip():
        raise ValueError("SOURCE_DATE_EPOCH or --source-date-epoch is required")
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError("SOURCE_DATE_EPOCH must be an integer") from exc
    if value < 315532800:
        raise ValueError("SOURCE_DATE_EPOCH must be representable by ZIP (1980 or later)")
    return value


def _zip_datetime(epoch: int) -> tuple[int, int, int, int, int, int]:
    value = list(time.gmtime(epoch)[:6])
    value[-1] -= value[-1] % 2
    return tuple(value)


def _verify_wheel_timestamp(path: Path, epoch: int) -> None:
    expected = _zip_datetime(epoch)
    with ZipFile(path) as archive:
        mismatched = [
            item.filename for item in archive.infolist() if item.date_time != expected
        ]
    if mismatched:
        sample = ", ".join(mismatched[:5])
        raise RuntimeError(
            f"{path.name} has timestamps outside SOURCE_DATE_EPOCH: {sample}"
        )


def _wheel_metadata(path: Path):
    with ZipFile(path) as archive:
        metadata_path = next(
            name for name in archive.namelist() if name.endswith(".dist-info/METADATA")
        )
        return Parser().parsestr(archive.read(metadata_path).decode("utf-8"))


def _stage_source(source: Path, destination: Path) -> None:
    generic_ignore = shutil.ignore_patterns(
        ".git",
        ".venv",
        ".aworld",
        ".agent",
        ".env",
        "__pycache__",
        ".pytest_cache",
        "*.egg-info",
        "failed_requests",
        "build",
        "tmp",
        "uv.lock",
    )
    source_root = source.resolve()

    def ignore(directory: str, names: list[str]) -> set[str]:
        ignored = set(generic_ignore(directory, names))
        # Only the repository-root release output is disposable.  The tracked
        # WebUI bundle lives at aworld/cmd/web/webui/dist and is required by
        # the legacy Runtime wheel.
        if Path(directory).resolve() == source_root and "dist" in names:
            ignored.add("dist")
        return ignored

    shutil.copytree(
        source,
        destination,
        ignore=ignore,
    )
    (destination / "pyproject.toml").unlink()
    shutil.copyfile(
        destination / CLI_MANIFEST,
        destination / "aworld-cli/pyproject.toml",
    )


def _build_command(args, source: Path, output: Path) -> list[str]:
    command = [args.uv, "build", "--python", args.python]
    if args.build_constraints is not None:
        command.extend(["--build-constraints", str(args.build_constraints.resolve())])
    command.extend(["--wheel", "--out-dir", str(output), str(source)])
    return command


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--outdir", type=Path, default=ROOT / "dist/runtime")
    parser.add_argument("--python", default=sys.executable)
    parser.add_argument("--uv", default="uv")
    parser.add_argument("--build-constraints", type=Path)
    parser.add_argument(
        "--source-date-epoch",
        default=os.environ.get("SOURCE_DATE_EPOCH"),
    )
    args = parser.parse_args(argv)
    try:
        epoch = _source_date_epoch(args.source_date_epoch)
    except ValueError as exc:
        parser.error(str(exc))

    core_version, cli_version = _source_versions(ROOT)
    output = args.outdir.expanduser().resolve()
    output.mkdir(parents=True, exist_ok=True)
    environment = dict(os.environ)
    environment.update(
        SOURCE_DATE_EPOCH=str(epoch),
        AWORLD_DISABLE_AUTO_DOTENV="1",
        AWORLD_EXTRA="framework",
        PYTHONHASHSEED="0",
    )

    with tempfile.TemporaryDirectory(prefix="aworld-runtime-build-") as temporary:
        staged = Path(temporary) / "source"
        _stage_source(ROOT, staged)
        subprocess.run(
            _build_command(args, staged, output),
            cwd=staged,
            env=environment,
            check=True,
        )
        subprocess.run(
            _build_command(args, staged / "aworld-cli", output),
            cwd=staged,
            env=environment,
            check=True,
        )

    core_wheel = output / f"aworld-{core_version}-py3-none-any.whl"
    cli_wheel = output / f"aworld_cli-{cli_version}-py3-none-any.whl"
    core_metadata = _wheel_metadata(core_wheel)
    cli_metadata = _wheel_metadata(cli_wheel)
    if core_metadata["Version"] != core_version:
        raise RuntimeError("Core wheel metadata does not match its source version")
    if cli_metadata["Version"] != cli_version:
        raise RuntimeError("CLI wheel metadata does not match its manifest version")
    if f"aworld=={core_version}" not in cli_metadata.get_all("Requires-Dist", []):
        raise RuntimeError("CLI wheel does not pin the exact core version")
    for wheel in (core_wheel, cli_wheel):
        _verify_wheel_timestamp(wheel, epoch)

    result = {
        "source_date_epoch": epoch,
        "artifacts": {
            wheel.name: hashlib.sha256(wheel.read_bytes()).hexdigest()
            for wheel in (core_wheel, cli_wheel)
        },
    }
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
