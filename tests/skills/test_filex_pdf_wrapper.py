from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
PARSE_SCRIPT = REPO_ROOT / "aworld-skills" / "filex-pdf" / "scripts" / "parse_pdf.py"


def _write_fake_filex(bin_dir: Path) -> Path:
    executable = bin_dir / "filex"
    executable.write_text(
        """#!/usr/bin/env python3
import json
import os
import pathlib
import sys

workspace = pathlib.Path(os.environ["FILEX_WORKSPACE_ROOT"])
result = workspace / "document_parse" / "fake-task" / "result.md"
result.parent.mkdir(parents=True, exist_ok=True)
result.write_text("# Parsed by FileX\\n", encoding="utf-8")
pathlib.Path(os.environ["FILEX_ARGS_LOG"]).write_text(
    json.dumps(sys.argv[1:]), encoding="utf-8"
)
print(json.dumps({
    "success": True,
    "task_id": "fake-task",
    "file_path": str(result.relative_to(workspace)),
    "metrics": {"provider": "liteparse"},
}))
""",
        encoding="utf-8",
    )
    executable.chmod(0o755)
    return executable


def test_filex_pdf_wrapper_runs_cli_and_materializes_markdown(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    source = workspace / "input.pdf"
    source.write_bytes(b"%PDF-1.4\nminimal fixture\n")
    output = workspace / "parsed" / "input.md"
    args_log = tmp_path / "filex-args.json"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _write_fake_filex(bin_dir)

    env = dict(os.environ)
    env["PATH"] = f"{bin_dir}{os.pathsep}{env.get('PATH', '')}"
    env["FILEX_WORKSPACE_ROOT"] = str(workspace)
    env["FILEX_ARGS_LOG"] = str(args_log)

    completed = subprocess.run(
        [
            sys.executable,
            str(PARSE_SCRIPT),
            "--input",
            str(source),
            "--output",
            str(output),
            "--provider",
            "liteparse",
            "--pages",
            "1,3-4",
            "--page-batch-size",
            "5",
        ],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )

    assert completed.returncode == 0, completed.stderr
    result = json.loads(completed.stdout)
    assert result["success"] is True
    assert result["output_path"] == str(output.resolve())
    assert output.read_text(encoding="utf-8") == "# Parsed by FileX\n"

    cli_args = json.loads(args_log.read_text(encoding="utf-8"))
    assert cli_args[:2] == ["parse", "--workspace-path"]
    assert str(source.resolve()) in cli_args
    file_type_index = cli_args.index("--file-type")
    assert cli_args[file_type_index : file_type_index + 2] == ["--file-type", "pdf"]
    pages_index = cli_args.index("--pages")
    assert cli_args[pages_index : pages_index + 2] == ["--pages", "1,3-4"]
    batch_index = cli_args.index("--page-batch-size")
    assert cli_args[batch_index : batch_index + 2] == ["--page-batch-size", "5"]
    env_payload = json.loads(cli_args[cli_args.index("--env-content-json") + 1])
    assert env_payload == {"filex_parse_provider": "liteparse"}


def test_filex_pdf_wrapper_rejects_non_pdf_before_calling_filex(tmp_path: Path) -> None:
    source = tmp_path / "not-a-pdf.pdf"
    source.write_text("plain text", encoding="utf-8")

    completed = subprocess.run(
        [
            sys.executable,
            str(PARSE_SCRIPT),
            "--input",
            str(source),
            "--workspace-root",
            str(tmp_path),
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 2
    payload = json.loads(completed.stdout)
    assert payload["success"] is False
    assert "PDF signature" in payload["message"]
