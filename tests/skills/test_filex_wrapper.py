from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

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
    result.write_text(
        os.environ.get("FILEX_FAKE_MARKDOWN", "# Parsed by FileX\\n"), encoding="utf-8"
    )
    document = result.with_suffix(".document.json")
    document.write_text(os.environ.get("FILEX_FAKE_DOCUMENT_JSON") or json.dumps({
        "schema_version": "filex-document-ir-v2",
        "coordinate_system": "pixel_top_left_xyxy",
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
    env["PYTHONPATH"] = (
        str(REPO_ROOT / "aworld-tools/filex/src")
        + os.pathsep
        + env.get("PYTHONPATH", "")
    )
    env["FILEX_PYTHON"] = sys.executable
    env.pop("FILEX_LAYOUT_FORMAT", None)
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
    assert result["artifacts"]["document"]["path"] == str(artifacts / "document.md")
    assert (artifacts / "document.md").read_text() == "# Parsed by FileX\n"
    assert (
        json.loads((artifacts / "layout.json").read_text())["pages"][0]["page_index"]
        == 0
    )


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


def _public_export(
    tmp_path: Path,
    *,
    flag: str | None = "parse-output",
    env_format: str | None = None,
    mutate_ir=None,
    markdown: str = "public\r\n",
    exporter_python: str | None = None,
):
    workspace, args_log, env = _environment(tmp_path)
    source = workspace / "input.pdf"
    source.write_bytes(b"%PDF public source")
    artifacts = tmp_path / "logs/artifacts"
    env["FILEX_ARTIFACTS_ROOT"] = str(artifacts)
    ir = {
        "schema_version": "filex-document-ir-v2",
        "coordinate_system": "pixel_top_left_xyxy",
        "pages": [
            {
                "page_index": 2,
                "width": 100,
                "height": 200,
                "elements": [
                    {
                        "type": "text",
                        "text": "public",
                        "bbox": [10, 20, 30, 60],
                        "reading_order": 1,
                    }
                ],
                "spans": [],
            }
        ],
    }
    if mutate_ir is not None:
        mutate_ir(ir)
    raw = json.dumps(ir, indent=2) + "\n"
    env["FILEX_FAKE_DOCUMENT_JSON"] = raw
    env["FILEX_FAKE_MARKDOWN"] = markdown
    if exporter_python is not None:
        env["FILEX_PYTHON"] = exporter_python
    if env_format is not None:
        env["FILEX_LAYOUT_FORMAT"] = env_format
    command = [
        sys.executable,
        str(FILEX_SCRIPT),
        "parse",
        "--input",
        str(source),
        "--pages",
        "3",
        "--artifacts-dir",
        str(artifacts),
    ]
    if flag is not None:
        command.extend(["--layout-format", flag])
    completed = subprocess.run(
        command, check=False, capture_output=True, text=True, env=env
    )
    return completed, artifacts, source, args_log, raw


def test_filex_wrapper_exports_parse_output_and_preserves_raw_ir_and_hashes(
    tmp_path: Path,
) -> None:
    completed, artifacts, source, args_log, raw = _public_export(tmp_path)
    assert completed.returncode == 0, completed.stdout + completed.stderr
    stdout = json.loads(completed.stdout)
    result = json.loads((artifacts / "result.json").read_bytes())
    assert stdout["layout_format"] == result["layout_format"] == "parse-output"
    assert result["schema_version"] == "filex.skill.parse-result/v2"
    assert (artifacts / "document-ir.json").read_bytes() == raw.encode()
    assert (artifacts / "document.md").read_bytes() == b"public\r\n"
    output = json.loads((artifacts / "layout.json").read_bytes())
    assert output["pages"][0]["page_index"] == 2
    assert output["layout_pages"][0]["page_number"] == 3
    assert output["markdown"] == "public\r\n"
    assert output["layout_pages"][0]["items"][0]["bbox"] == {
        "x": 10,
        "y": 20,
        "w": 20,
        "h": 40,
        "label": "text",
    }
    for item in [result["source"], *result["artifacts"].values()]:
        content = Path(item["path"]).read_bytes()
        assert item["size"] == len(content)
        assert item["sha256"] == "sha256:" + hashlib.sha256(content).hexdigest()
    assert result["source"]["path"] == str(source)
    assert "input_path" not in result["filex"]  # Unmodified FileX control response.
    assert result["filex"]["metrics"] == {
        "provider": "python_docx",
        "provider_version": "1",
    }
    cli_args = json.loads(args_log.read_bytes())
    assert cli_args[cli_args.index("--pages") + 1] == "3"
    assert "--layout-format" not in cli_args


@pytest.mark.parametrize(
    ("flag", "env_format", "expected"),
    [
        (None, "parse-output", "parse-output"),
        ("document-ir", "parse-output", "document-ir"),
        ("parse-output", "document-ir", "parse-output"),
    ],
)
def test_filex_wrapper_layout_flag_overrides_runtime_default(
    tmp_path: Path, flag: str | None, env_format: str, expected: str
) -> None:
    completed, artifacts, _, _, _ = _public_export(
        tmp_path, flag=flag, env_format=env_format
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert json.loads(completed.stdout)["layout_format"] == expected
    result = json.loads((artifacts / "result.json").read_bytes())
    if expected == "document-ir":
        assert result["schema_version"] == "filex.skill.parse-result/v1"
        assert set(result["artifacts"]) == {"document", "layout"}
        assert not (artifacts / "document-ir.json").exists()


def test_filex_wrapper_rejects_invalid_runtime_layout_default_before_parsing(
    tmp_path: Path,
) -> None:
    completed, _, _, args_log, _ = _public_export(
        tmp_path, flag=None, env_format="unknown"
    )
    assert completed.returncode == 2
    assert json.loads(completed.stdout)["error_type"] == "InputError"
    assert not args_log.exists()


def test_filex_output_is_accepted_by_public_verifier_without_filex_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    completed, artifacts, source, _, _ = _public_export(tmp_path)
    assert completed.returncode == 0, completed.stdout + completed.stderr
    monkeypatch.syspath_prepend(str(REPO_ROOT / "benchmark-datasets/parsebench/tools"))
    from parsebench_dataset import verifier
    from parsebench_dataset.contracts import ParseBenchDimension

    truth = verifier._GroundTruth(
        task_id="public-task",
        dimensions=(ParseBenchDimension.LAYOUT,),
        rules=(),
        source=verifier._GroundTruthSource(
            logical_path="docs/input.pdf",
            runtime_path=source,
            size=source.stat().st_size,
            sha256="sha256:" + hashlib.sha256(source.read_bytes()).hexdigest(),
            page=3,
        ),
    )
    verifier._validate_source_integrity(truth.source, workspace_root=source.parent)
    (
        artifacts / "result.json"
    ).unlink()  # The public contract needs no producer receipt.
    snapshot = verifier._snapshot_artifacts(
        ground_truth=truth,
        result_path=artifacts / "result.json",
        markdown_path=artifacts / "document.md",
        layout_path=artifacts / "layout.json",
        workspace_root=source.parent,
    )
    assert snapshot.layout["markdown"] == "public\r\n"
    assert snapshot.layout["layout_pages"][2]["page_number"] == 3
    assert snapshot.layout["layout_pages"][2]["items"][0]["bbox"]["x"] == 10


def test_filex_wrapper_preserves_actual_empty_document_output(tmp_path: Path) -> None:
    completed, artifacts, _, _, _ = _public_export(
        tmp_path,
        markdown="",
        mutate_ir=lambda ir: ir.update(pages=[]),
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert (artifacts / "document.md").read_bytes() == b""
    assert json.loads((artifacts / "layout.json").read_bytes())["layout_pages"] == []


def test_filex_wrapper_reports_invalid_geometry_without_committing_receipt(
    tmp_path: Path,
) -> None:
    completed, artifacts, _, _, _ = _public_export(
        tmp_path,
        mutate_ir=lambda ir: ir["pages"][0]["elements"][0].update(bbox=[]),
    )
    assert completed.returncode == 2
    response = json.loads(completed.stdout)
    assert response["error_type"] == "OutputError"
    assert "element bbox" in response["message"]
    assert not (artifacts / "result.json").exists()
    assert not (artifacts / "layout.json").exists()


def test_filex_wrapper_uses_the_configured_exporter_python(tmp_path: Path) -> None:
    completed, artifacts, _, _, _ = _public_export(
        tmp_path, exporter_python="/missing-filex-python"
    )
    assert completed.returncode == 2
    assert "FILEX_PYTHON" in json.loads(completed.stdout)["message"]
    assert not (artifacts / "result.json").exists()


@pytest.mark.docker_integration
def test_exported_filex_page_is_scored_by_the_public_official_verifier(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    image = os.environ.get("PARSEBENCH_SCORER_TEST_IMAGE")
    if not image:
        pytest.skip("Set PARSEBENCH_SCORER_TEST_IMAGE for offline official scoring")
    completed, artifacts, source, _, _ = _public_export(tmp_path)
    assert completed.returncode == 0, completed.stdout + completed.stderr
    (artifacts / "result.json").unlink()
    monkeypatch.syspath_prepend(str(REPO_ROOT / "benchmark-datasets/parsebench/tools"))
    from parsebench_dataset.contracts import (
        DATASET_REVISION,
        GROUND_TRUTH_SCHEMA_VERSION,
        SCORER_REVISION,
    )
    from parsebench_dataset.dataset import _verifier_runtime_modules

    tests = tmp_path / "tests"
    package = tests / "parsebench_verifier"
    package.mkdir(parents=True)
    for name, content in _verifier_runtime_modules().items():
        if name != "artifacts.py":
            (package / name).write_bytes(content)
    (tests / "ground_truth.json").write_text(
        json.dumps(
            {
                "schema_version": GROUND_TRUTH_SCHEMA_VERSION,
                "dataset_revision": DATASET_REVISION,
                "scorer_revision": SCORER_REVISION,
                "task_id": "public-page-3",
                "dimensions": ["layout"],
                "source": {
                    "path": "docs/input.pdf",
                    "runtime_path": "/workspace/input.pdf",
                    "size": source.stat().st_size,
                    "sha256": "sha256:"
                    + hashlib.sha256(source.read_bytes()).hexdigest(),
                    "page": 3,
                },
                "rules": [
                    {
                        "id": "public-layout",
                        "dimension": "layout",
                        "type": "layout",
                        "rule": {
                            "bbox": [0.1, 0.1, 0.2, 0.2],
                            "canonical_class": "Text",
                            "content": {"type": "text", "text": "public"},
                        },
                        "page": 3,
                        "expected_markdown": None,
                        "tags": [],
                        "provenance": {
                            "source_jsonl": "layout.jsonl",
                            "source_line": 1,
                        },
                    }
                ],
            }
        )
    )
    (tests / "parsebench-scope.json").write_text(
        json.dumps(
            {
                "schema_version": "aworld-parsebench-scope/v1",
                "kind": "smoke",
                "selection_manifest_sha256": "sha256:" + "0" * 64,
                "publishable": False,
                "non_publishable_reasons": ["smoke_selection"],
                "selected_execution_count": 1,
                "runtime_image": "python:3.12-slim",
                "dataset_revision": DATASET_REVISION,
                "scorer_revision": SCORER_REVISION,
            }
        )
    )
    (tests / "run.py").write_text("""from pathlib import Path
from parsebench_verifier.verifier import verify_parsebench_task
outcome = verify_parsebench_task(
    ground_truth_path=Path("/case/tests/ground_truth.json"),
    markdown_path=Path("/case/logs/artifacts/document.md"),
    layout_path=Path("/case/logs/artifacts/layout.json"),
    workspace_root=Path("/case/workspace"),
    verifier_output=Path("/case/verifier"),
)
print(outcome.stdout_line)
assert outcome.result.reward_payload()["parsebench_layout_score"] == 1.0
""")
    created = subprocess.run(
        [
            "docker",
            "create",
            "--network",
            "none",
            image,
            "python",
            "/case/tests/run.py",
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    container = created.stdout.strip()
    try:
        subprocess.run(
            ["docker", "cp", str(tmp_path), f"{container}:/case"],
            check=True,
            capture_output=True,
            timeout=30,
        )
        result = subprocess.run(
            ["docker", "start", "-a", container],
            check=False,
            capture_output=True,
            text=True,
            timeout=120,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        subprocess.run(
            ["docker", "cp", f"{container}:/case/verifier", str(tmp_path / "verifier")],
            check=True,
            capture_output=True,
            timeout=30,
        )
        reward = json.loads((tmp_path / "verifier/reward.json").read_bytes())
        assert reward["parsebench_layout_score"] == 1.0
    finally:
        subprocess.run(
            ["docker", "rm", "-f", container],
            check=True,
            capture_output=True,
            timeout=30,
        )
