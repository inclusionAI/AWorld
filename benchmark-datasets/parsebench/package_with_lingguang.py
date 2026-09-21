#!/usr/bin/env python3
"""Build a manual-upload ZIP with an unmodified lingguang-bench-client."""

from __future__ import annotations

import argparse
import json
import os
import tempfile
from pathlib import Path

from lingguang_bench.config import BenchConfig
from lingguang_bench.dataset import build_gateway_package


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    arguments = parser.parse_args()
    project = arguments.project.expanduser().resolve()
    output = arguments.output.expanduser().resolve()
    package = build_gateway_package(BenchConfig.load(project / "bench.toml"))
    output.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{output.name}.", dir=output.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as destination:
            destination.write(package.content)
            destination.flush()
            os.fsync(destination.fileno())
        os.replace(temporary, output)
    finally:
        temporary.unlink(missing_ok=True)
    print(
        json.dumps(
            {
                "output": str(output),
                "sha256": package.content_sha256,
                "package_kind": package.package_kind,
                "task_count": package.task_count,
                "sample_count": package.sample_count,
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
