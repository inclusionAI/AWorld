from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
FILEX_SCRIPT = REPO_ROOT / "aworld-skills" / "filex" / "scripts" / "filex.py"


def _write_fake_filex(bin_dir: Path) -> Path:
    executable = bin_dir / "filex"
    executable.write_text(
        """#!/usr/bin/env python3
import json
import os
import pathlib
import sys

args = sys.argv[1:]
pathlib.Path(os.environ["FILEX_ARGS_LOG"]).write_text(
    json.dumps(args), encoding="utf-8"
)
workspace = pathlib.Path(os.environ["FILEX_WORKSPACE_ROOT"])

if args[0] == "parse":
    result = workspace / "document_parse" / "fake-task" / "result.md"
    result.parent.mkdir(parents=True, exist_ok=True)
    result.write_text("# Parsed by FileX\\n", encoding="utf-8")
    document = result.with_suffix(".document.json")
    document.write_text(json.dumps({
        "schema_version": "filex.document-ir/v1",
        "coordinate_system": "pixel_xyxy",
        "pages": [{"page_index": 0, "width": 100, "height": 200, "elements": []}],
    }), encoding="utf-8")
    payload = {
        "success": True,
        "task_id": "fake-task",
        "file_path": str(result.relative_to(workspace)),
        "document_file_path": str(document.relative_to(workspace)),
        "metrics": {"provider": "python_docx", "provider_version": "1"},
    }
elif args[0] == "inspect":
    payload = {
        "success": True,
        "source_provider": "youtube",
        "recommended_route": ["youtube_subtitle", "local_whisper"],
    }
else:
    payload = {"success": True, "status": "parsing", "completed_batches": 2}

print(json.dumps(payload))
""",
        encoding="utf-8",
    )
    executable.chmod(0o755)
    return executable


def _environment(tmp_path: Path) -> tuple[Path, Path, dict[str, str]]:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    args_log = tmp_path / "filex-args.json"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _write_fake_filex(bin_dir)
    env = dict(os.environ)
    env["PATH"] = f"{bin_dir}{os.pathsep}{env.get('PATH', '')}"
    env["FILEX_WORKSPACE_ROOT"] = str(workspace)
    env["FILEX_ARGS_LOG"] = str(args_log)
    return workspace, args_log, env


def test_filex_wrapper_parses_any_supported_local_file(tmp_path: Path) -> None:
    workspace, args_log, env = _environment(tmp_path)
    source = workspace / "input.docx"
    source.write_bytes(b"fake office document")
    output = workspace / "parsed" / "input.md"

    completed = subprocess.run(
        [
            sys.executable,
            str(FILEX_SCRIPT),
            "parse",
            "--input",
            str(source),
            "--output",
            str(output),
            "--file-type",
            "docx",
            "--provider",
            "python_docx",
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
    assert cli_args[0] == "parse"
    assert cli_args[cli_args.index("--file-type") + 1] == "docx"
    provider = json.loads(cli_args[cli_args.index("--env-content-json") + 1])
    assert provider == {"filex_parse_provider": "python_docx"}


def test_filex_wrapper_parses_url_and_passes_env_file_without_exposing_secret(
    tmp_path: Path,
) -> None:
    workspace, args_log, env = _environment(tmp_path)
    env_file = workspace / "filex-env.json"
    secret = "sensitive-test-value"
    env_file.write_text(
        json.dumps({"gateway_vllm": {"api_key": secret}}), encoding="utf-8"
    )
    output = workspace / "parsed" / "remote.md"

    completed = subprocess.run(
        [
            sys.executable,
            str(FILEX_SCRIPT),
            "parse",
            "--url",
            "https://example.com/report.pdf",
            "--output",
            str(output),
            "--env-file",
            str(env_file),
        ],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )

    assert completed.returncode == 0
    cli_args = json.loads(args_log.read_text(encoding="utf-8"))
    assert cli_args[cli_args.index("--url") + 1] == "https://example.com/report.pdf"
    assert "--env-content-file" in cli_args
    assert secret not in json.dumps(cli_args)
    assert output.read_text(encoding="utf-8") == "# Parsed by FileX\n"


def test_filex_wrapper_reads_batch_status(tmp_path: Path) -> None:
    _, args_log, env = _environment(tmp_path)
    completed = subprocess.run(
        [
            sys.executable,
            str(FILEX_SCRIPT),
            "status",
            "--batch-resume-id",
            "stable-id",
            "--include-results",
        ],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )

    assert completed.returncode == 0
    assert json.loads(completed.stdout)["completed_batches"] == 2
    cli_args = json.loads(args_log.read_text(encoding="utf-8"))
    assert cli_args[:3] == ["status", "--batch-resume-id", "stable-id"]
    assert "--include-results" in cli_args


def test_filex_wrapper_exports_generic_artifact_bundle(tmp_path: Path) -> None:
    workspace, _, env = _environment(tmp_path)
    source = workspace / "input.pdf"
    source.write_bytes(b"%PDF-test")
    artifacts = tmp_path / "logs" / "artifacts"
    env["FILEX_ARTIFACTS_ROOT"] = str(artifacts)

    completed = subprocess.run(
        [
            sys.executable,
            str(FILEX_SCRIPT),
            "parse",
            "--input",
            str(source),
            "--artifacts-dir",
            str(artifacts),
        ],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )

    assert completed.returncode == 0, completed.stdout
    result = json.loads((artifacts / "result.json").read_text())
    assert result["schema_version"] == "filex.skill.parse-result/v1"
    assert result["source"]["sha256"].startswith("sha256:")
    assert result["artifacts"]["document"]["path"] == str(
        artifacts / "document.md"
    )
    assert (artifacts / "document.md").read_text() == "# Parsed by FileX\n"
    assert json.loads((artifacts / "layout.json").read_text())["pages"][0][
        "page_index"
    ] == 0


def test_filex_wrapper_inspects_youtube_without_media_download(tmp_path: Path) -> None:
    _, args_log, env = _environment(tmp_path)
    url = "https://www.youtube.com/watch?v=abc123"

    completed = subprocess.run(
        [sys.executable, str(FILEX_SCRIPT), "inspect", "--url", url],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )

    assert completed.returncode == 0
    payload = json.loads(completed.stdout)
    assert payload["source_provider"] == "youtube"
    cli_args = json.loads(args_log.read_text(encoding="utf-8"))
    assert cli_args == ["inspect", url]


def test_filex_wrapper_forwards_youtube_transcript_options(tmp_path: Path) -> None:
    workspace, args_log, env = _environment(tmp_path)
    output = workspace / "parsed" / "youtube.md"

    completed = subprocess.run(
        [
            sys.executable,
            str(FILEX_SCRIPT),
            "parse",
            "--url",
            "https://www.youtube.com/watch?v=abc123",
            "--output",
            str(output),
            "--mode",
            "transcript",
            "--language",
            "en",
            "--allow-media-download",
            "--rights-basis",
            "user-owned",
        ],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )

    assert completed.returncode == 0
    cli_args = json.loads(args_log.read_text(encoding="utf-8"))
    assert cli_args[cli_args.index("--mode") + 1] == "transcript"
    assert cli_args[cli_args.index("--language") + 1] == "en"
    assert "--allow-media-download" in cli_args
    assert cli_args[cli_args.index("--rights-basis") + 1] == "user-owned"


def test_filex_wrapper_rejects_local_input_outside_workspace(tmp_path: Path) -> None:
    _, _, env = _environment(tmp_path)
    source = tmp_path / "outside.pdf"
    source.write_bytes(b"%PDF-1.4\n")

    completed = subprocess.run(
        [sys.executable, str(FILEX_SCRIPT), "parse", "--input", str(source)],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )

    assert completed.returncode == 2
    assert "inside the FileX workspace" in json.loads(completed.stdout)["message"]
