from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "aworld-cli" / "src"))

from aworld_cli.parsebench_gateway import (
    DatasetImageBuildReceipt,
    DatasetPublicationReceipt,
    GatewayRunManifest,
    ParseBenchGatewayError,
    ParseBenchPackageDescriptor,
    atomic_write_json,
    batch_acceptance_checksum,
    bind_submission_intent,
    build_submission_intent,
    finalize_json_output,
    load_gateway_run_manifest,
    reserve_json_output,
)
from aworld_cli.top_level_commands.benchmark_cmd import BenchmarkTopLevelCommand

from aworld.benchmarks.parsebench.contracts import (
    PackageBuildResult,
    ParseBenchDimension,
    SmokeSelection,
)


def _parser_and_command() -> tuple[argparse.ArgumentParser, BenchmarkTopLevelCommand]:
    command = BenchmarkTopLevelCommand()
    parser = argparse.ArgumentParser(prog="aworld-cli")
    subparsers = parser.add_subparsers(dest="command", required=True)
    command.register_parser(subparsers)
    return parser, command


def _package(tmp_path: Path) -> ParseBenchPackageDescriptor:
    package_path = tmp_path / "parsebench.zip"
    package_path.write_bytes(b"fixture")
    package_stat = package_path.stat()
    task_id = "pb-command-task"
    return ParseBenchPackageDescriptor(
        package_path=package_path,
        package_sha256="sha256:" + "1" * 64,
        dataset_id="parsebench-command-smoke",
        service_name="aworld-filex-parsebench",
        task_ids=(task_id,),
        expected_case_ids={
            dimension: ((task_id,) if dimension is ParseBenchDimension.TABLE else ())
            for dimension in ParseBenchDimension
        },
        selection_kind="smoke",
        selection_digest="sha256:" + "2" * 64,
        publishable=False,
        runtime_image="aworld-filex-parsebench:test",
        package_device=package_stat.st_dev,
        package_inode=package_stat.st_ino,
        package_size=package_stat.st_size,
        package_mtime_ns=package_stat.st_mtime_ns,
        package_ctime_ns=package_stat.st_ctime_ns,
    )


def _publication() -> DatasetPublicationReceipt:
    return DatasetPublicationReceipt(
        generation=1,
        root_modify_time=1,
        content_sha256="sha256:" + "3" * 64,
        task_set_sha256="sha256:" + "4" * 64,
    )


def _image_receipt(package: ParseBenchPackageDescriptor) -> DatasetImageBuildReceipt:
    return DatasetImageBuildReceipt(
        dataset_id=package.dataset_id,
        service_name=package.service_name,
        dataset_generation=_publication().generation,
        task_set_sha256=_publication().task_set_sha256,
        selected_task_ids=package.task_ids,
    )


def _run_manifest(tmp_path: Path) -> tuple[Path, GatewayRunManifest]:
    package = _package(tmp_path)
    manifest = GatewayRunManifest.from_submission(
        package,
        selected_task_ids=package.task_ids,
        model_profile="default__gemini-3.1-pro-preview",
        publication=_publication(),
        image_build=_image_receipt(package),
        gateway_url="https://gateway.example.test/",
        client_request_id="parsebench-command-request",
        response={
            "batch_id": "batch-command",
            "total": 1,
            "run_ids": ["run-1"],
            "acceptance_checksum": batch_acceptance_checksum(
                batch_id="batch-command", run_ids=["run-1"]
            ),
        },
    )
    path = tmp_path / "parsebench-run.json"
    atomic_write_json(path, manifest.to_dict())
    return path, manifest


def test_prepare_defaults_to_bounded_smoke_selection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    parser, command = _parser_and_command()
    captured: dict[str, object] = {}

    def fake_build(source, output, **kwargs):
        captured.update({"source": source, "output": output, **kwargs})
        return PackageBuildResult(
            output=Path(output).resolve(),
            package_sha256="sha256:" + "a" * 64,
            task_count=5,
            rule_count=9,
            dimensions=tuple(ParseBenchDimension),
            selection=kwargs["selection"],
            publishable=False,
            selection_manifest_sha256="sha256:" + "b" * 64,
        )

    monkeypatch.setattr(
        "aworld_cli.top_level_commands.benchmark_cmd.build_parsebench_executable_dataset",
        fake_build,
    )
    args = parser.parse_args(
        [
            "benchmark",
            "parsebench",
            "prepare",
            "--source",
            str(tmp_path / "source"),
            "--output",
            str(tmp_path / "smoke.zip"),
            "--runtime-image",
            "aworld-filex-parsebench:test",
            "--allow-mutable-local-image",
        ]
    )

    assert command.run(args, None) == 0
    selection = captured["selection"]
    assert isinstance(selection, SmokeSelection)
    assert selection.per_dimension == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["schema"] == "aworld.parsebench.prepare/v1"
    assert payload["selection_kind"] == "smoke"
    assert payload["task_count"] == 5
    assert payload["publishable"] is False


def test_submit_writes_secret_free_versioned_run_manifest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    parser, command = _parser_and_command()
    package = _package(tmp_path)
    secret = "deployment-bearer-secret"
    observed: dict[str, object] = {}

    class FakeGatewayClient:
        def __init__(self, gateway_url, **kwargs):
            observed.update({"gateway_url": gateway_url, **kwargs})

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def import_package(self, value, *, client_request_id):
            assert value is package
            observed["import_request_id"] = client_request_id
            return _publication()

        def prepare_task_images(self, value, **kwargs):
            assert value is package
            observed["image_build"] = kwargs
            return _image_receipt(package)

        def submit(self, payload):
            observed["payload"] = payload
            return {
                "batch_id": "batch-command",
                "total": 1,
                "run_ids": ["run-1"],
                "acceptance_checksum": batch_acceptance_checksum(
                    batch_id="batch-command", run_ids=["run-1"]
                ),
            }

    monkeypatch.setattr(
        "aworld_cli.top_level_commands.benchmark_cmd.inspect_parsebench_package",
        lambda _path: package,
    )
    monkeypatch.setattr(
        "aworld_cli.top_level_commands.benchmark_cmd.ParseBenchGatewayClient",
        FakeGatewayClient,
    )
    monkeypatch.setenv("TEST_PARSEBENCH_TOKEN", secret)
    run_manifest = tmp_path / "run.json"
    args = parser.parse_args(
        [
            "benchmark",
            "parsebench",
            "submit",
            "--package",
            str(package.package_path),
            "--run-manifest",
            str(run_manifest),
            "--gateway-url",
            "https://gateway.example.test",
            "--token-env",
            "TEST_PARSEBENCH_TOKEN",
        ]
    )

    assert command.run(args, None) == 0
    assert observed["token"] == secret
    request = observed["payload"]
    assert observed["import_request_id"] == request["client_request_id"]
    assert observed["image_build"]["publication"] == _publication()
    assert request["scheduler_config"] == {
        "execution_environment": "agent_only",
        "harness_profile": "aworld",
        "model_profile": "default__gemini-3.1-pro-preview",
    }
    loaded = load_gateway_run_manifest(run_manifest)
    assert loaded.batch_id == "batch-command"
    rendered = capsys.readouterr().out + run_manifest.read_text(encoding="utf-8")
    assert secret not in rendered


def test_status_and_report_use_versioned_manifest_and_bounded_results(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    parser, command = _parser_and_command()
    run_path, manifest = _run_manifest(tmp_path)
    results = {
        "batch_id": manifest.batch_id,
        "status": "done",
        "total": 1,
        "completed": 1,
        "failed": 0,
        "results": [],
    }
    observed: list[tuple[str, str, int | None]] = []

    class FakeGatewayClient:
        def __init__(self, _gateway_url, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def batch_status(self, batch_id, *, expected_total):
            observed.append(("status", batch_id, expected_total))
            return results

        def batch_results(self, batch_id, *, page_size, expected_total):
            observed.append((f"results:{page_size}", batch_id, expected_total))
            return results

    monkeypatch.setattr(
        "aworld_cli.top_level_commands.benchmark_cmd.ParseBenchGatewayClient",
        FakeGatewayClient,
    )
    monkeypatch.setattr(
        "aworld_cli.top_level_commands.benchmark_cmd.reduce_gateway_batch_results",
        lambda run_manifest, raw: {
            "schema": "aworld.parsebench.gateway-report/v2",
            "batch_id": run_manifest.batch_id,
            "status": "scored",
            "publishable": False,
            "overall_score": 0.75,
            "raw_status": raw["status"],
        },
    )
    common = [
        "--run-manifest",
        str(run_path),
        "--gateway-url",
        "https://gateway.example.test",
    ]
    status_args = parser.parse_args(["benchmark", "parsebench", "status", *common])
    assert command.run(status_args, None) == 0
    status_payload = json.loads(capsys.readouterr().out)
    assert status_payload["terminal"] is True
    assert status_payload["completed"] == 1

    report_path = tmp_path / "report.json"
    report_args = parser.parse_args(
        [
            "benchmark",
            "parsebench",
            "report",
            *common,
            "--page-size",
            "17",
            "--output",
            str(report_path),
        ]
    )
    assert command.run(report_args, None) == 0
    report_payload = json.loads(report_path.read_text(encoding="utf-8"))
    assert report_payload["overall_score"] == 0.75
    assert report_payload["publishable"] is False
    assert observed == [
        ("status", manifest.batch_id, 1),
        ("status", manifest.batch_id, 1),
        ("results:17", manifest.batch_id, 1),
    ]


def test_gateway_run_manifest_loader_requires_canonical_json(tmp_path: Path) -> None:
    path, _manifest = _run_manifest(tmp_path)
    value = json.loads(path.read_text(encoding="utf-8"))
    path.write_text(json.dumps(value, indent=2), encoding="utf-8")

    with pytest.raises(ParseBenchGatewayError) as captured:
        load_gateway_run_manifest(path)

    assert getattr(captured.value, "code", None) == "run_manifest_invalid"


def test_gateway_run_manifest_loader_rejects_a_symlink(tmp_path: Path) -> None:
    path, _manifest = _run_manifest(tmp_path)
    link = tmp_path / "linked-run.json"
    link.symlink_to(path)

    with pytest.raises(ParseBenchGatewayError) as captured:
        load_gateway_run_manifest(link)

    assert captured.value.code == "run_manifest_invalid"


def test_submit_resumes_an_imported_intent_with_the_same_idempotency_key(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    parser, command = _parser_and_command()
    package = _package(tmp_path)
    run_manifest = tmp_path / "recoverable-run.json"
    observed_keys: list[str] = []
    import_calls = 0

    class FakeGatewayClient:
        def __init__(self, _gateway_url, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def import_package(self, _package, *, client_request_id):
            nonlocal import_calls
            assert client_request_id
            import_calls += 1
            return _publication()

        def prepare_task_images(self, _package, **_kwargs):
            return _image_receipt(package)

        def submit(self, payload):
            observed_keys.append(payload["client_request_id"])
            if len(observed_keys) == 1:
                raise ParseBenchGatewayError(
                    "gateway_unavailable", "simulated accepted response loss"
                )
            run_ids = ["run-recovered"]
            return {
                "batch_id": "batch-recovered",
                "total": 1,
                "run_ids": run_ids,
                "acceptance_checksum": batch_acceptance_checksum(
                    batch_id="batch-recovered", run_ids=run_ids
                ),
            }

    monkeypatch.setattr(
        "aworld_cli.top_level_commands.benchmark_cmd.inspect_parsebench_package",
        lambda _path: package,
    )
    monkeypatch.setattr(
        "aworld_cli.top_level_commands.benchmark_cmd.ParseBenchGatewayClient",
        FakeGatewayClient,
    )
    common = [
        "benchmark",
        "parsebench",
        "submit",
        "--package",
        str(package.package_path),
        "--run-manifest",
        str(run_manifest),
        "--gateway-url",
        "https://gateway.example.test",
    ]

    assert command.run(parser.parse_args(common), None) == 2
    saved_intent = json.loads(run_manifest.read_text(encoding="utf-8"))
    assert saved_intent["status"] == "images_ready"
    assert saved_intent["dataset_publication"] == _publication().to_dict()
    assert saved_intent["image_build_receipt"] == _image_receipt(package).to_dict()
    capsys.readouterr()

    assert command.run(parser.parse_args([*common, "--resume"]), None) == 0
    recovered = load_gateway_run_manifest(run_manifest)
    assert recovered.batch_id == "batch-recovered"
    assert import_calls == 1
    assert observed_keys == [recovered.client_request_id] * 2


def test_submit_resumes_package_import_with_the_same_idempotency_key(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    parser, command = _parser_and_command()
    package = _package(tmp_path)
    run_manifest = tmp_path / "recoverable-import.json"
    import_keys: list[str] = []
    submit_calls = 0

    class FakeGatewayClient:
        def __init__(self, _gateway_url, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def import_package(self, _package, *, client_request_id):
            import_keys.append(client_request_id)
            if len(import_keys) == 1:
                raise ParseBenchGatewayError(
                    "gateway_unavailable", "simulated accepted import response loss"
                )
            return _publication()

        def prepare_task_images(self, _package, **_kwargs):
            return _image_receipt(package)

        def submit(self, payload):
            nonlocal submit_calls
            submit_calls += 1
            run_ids = ["run-after-import-recovery"]
            return {
                "batch_id": "batch-after-import-recovery",
                "total": 1,
                "run_ids": run_ids,
                "acceptance_checksum": batch_acceptance_checksum(
                    batch_id="batch-after-import-recovery", run_ids=run_ids
                ),
            }

    monkeypatch.setattr(
        "aworld_cli.top_level_commands.benchmark_cmd.inspect_parsebench_package",
        lambda _path: package,
    )
    monkeypatch.setattr(
        "aworld_cli.top_level_commands.benchmark_cmd.ParseBenchGatewayClient",
        FakeGatewayClient,
    )
    common = [
        "benchmark",
        "parsebench",
        "submit",
        "--package",
        str(package.package_path),
        "--run-manifest",
        str(run_manifest),
        "--gateway-url",
        "https://gateway.example.test",
    ]

    assert command.run(parser.parse_args(common), None) == 2
    saved_intent = json.loads(run_manifest.read_text(encoding="utf-8"))
    assert saved_intent["status"] == "submitting"
    capsys.readouterr()

    assert command.run(parser.parse_args([*common, "--resume"]), None) == 0
    recovered = load_gateway_run_manifest(run_manifest)
    assert import_keys == [recovered.client_request_id] * 2
    assert recovered.dataset_publication == _publication()
    assert submit_calls == 1


def test_submit_resumes_image_preparation_from_imported_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parser, command = _parser_and_command()
    package = _package(tmp_path)
    run_manifest = tmp_path / "recoverable-images.json"
    import_calls = 0
    image_calls = 0

    class FakeGatewayClient:
        def __init__(self, _gateway_url, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def import_package(self, _package, *, client_request_id):
            nonlocal import_calls
            assert client_request_id
            import_calls += 1
            return _publication()

        def prepare_task_images(self, _package, **_kwargs):
            nonlocal image_calls
            image_calls += 1
            if image_calls == 1:
                raise ParseBenchGatewayError(
                    "gateway_unavailable",
                    "simulated image build response loss",
                )
            return _image_receipt(package)

        def submit(self, _payload):
            run_ids = ["run-after-image-recovery"]
            return {
                "batch_id": "batch-after-image-recovery",
                "total": 1,
                "run_ids": run_ids,
                "acceptance_checksum": batch_acceptance_checksum(
                    batch_id="batch-after-image-recovery", run_ids=run_ids
                ),
            }

    monkeypatch.setattr(
        "aworld_cli.top_level_commands.benchmark_cmd.inspect_parsebench_package",
        lambda _path: package,
    )
    monkeypatch.setattr(
        "aworld_cli.top_level_commands.benchmark_cmd.ParseBenchGatewayClient",
        FakeGatewayClient,
    )
    common = [
        "benchmark",
        "parsebench",
        "submit",
        "--package",
        str(package.package_path),
        "--run-manifest",
        str(run_manifest),
        "--gateway-url",
        "https://gateway.example.test",
    ]

    assert command.run(parser.parse_args(common), None) == 2
    interrupted = json.loads(run_manifest.read_text(encoding="utf-8"))
    assert interrupted["status"] == "imported"
    assert interrupted["dataset_publication"] == _publication().to_dict()

    assert command.run(parser.parse_args([*common, "--resume"]), None) == 0
    assert load_gateway_run_manifest(run_manifest).batch_id == (
        "batch-after-image-recovery"
    )
    assert import_calls == 1
    assert image_calls == 2


def test_resume_rejects_a_different_gateway_before_any_client_call(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parser, command = _parser_and_command()
    package = _package(tmp_path)
    intent = build_submission_intent(
        package,
        selected_task_ids=package.task_ids,
        model_profile="default__gemini-3.1-pro-preview",
        timeout_seconds=3_600,
        gateway_url="https://gateway-a.example.test",
    )
    run_manifest = tmp_path / "resume.json"
    reserve_json_output(run_manifest, intent)
    bound = bind_submission_intent(intent, _publication())
    finalize_json_output(run_manifest, reservation=intent, value=bound)
    client_calls = 0

    class ForbiddenGatewayClient:
        def __init__(self, *_args, **_kwargs):
            nonlocal client_calls
            client_calls += 1

    monkeypatch.setattr(
        "aworld_cli.top_level_commands.benchmark_cmd.inspect_parsebench_package",
        lambda _path: package,
    )
    monkeypatch.setattr(
        "aworld_cli.top_level_commands.benchmark_cmd.ParseBenchGatewayClient",
        ForbiddenGatewayClient,
    )
    args = parser.parse_args(
        [
            "benchmark",
            "parsebench",
            "submit",
            "--package",
            str(package.package_path),
            "--run-manifest",
            str(run_manifest),
            "--gateway-url",
            "https://gateway-b.example.test",
            "--resume",
        ]
    )

    assert command.run(args, None) == 2
    assert client_calls == 0
    assert json.loads(run_manifest.read_text(encoding="utf-8")) == bound


def test_submit_preflight_errors_have_zero_remote_side_effects(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parser, command = _parser_and_command()
    package = _package(tmp_path)
    remote_calls = 0

    class FakeGatewayClient:
        def __init__(self, *_args, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def import_package(self, _package, *, client_request_id):
            nonlocal remote_calls
            assert client_request_id
            remote_calls += 1
            raise AssertionError("unexpected import")

    monkeypatch.setattr(
        "aworld_cli.top_level_commands.benchmark_cmd.inspect_parsebench_package",
        lambda _path: package,
    )
    monkeypatch.setattr(
        "aworld_cli.top_level_commands.benchmark_cmd.ParseBenchGatewayClient",
        FakeGatewayClient,
    )
    existing = tmp_path / "existing.json"
    existing.write_text("owner", encoding="utf-8")
    common = [
        "benchmark",
        "parsebench",
        "submit",
        "--package",
        str(package.package_path),
        "--gateway-url",
        "https://gateway.example.test",
    ]

    invalid_model = parser.parse_args(
        [
            *common,
            "--run-manifest",
            str(tmp_path / "invalid.json"),
            "--model-profile",
            "org/model",
        ]
    )
    assert command.run(invalid_model, None) == 2
    invalid_image_timeout = parser.parse_args(
        [
            *common,
            "--run-manifest",
            str(tmp_path / "invalid-timeout.json"),
            "--image-build-timeout",
            "0",
        ]
    )
    assert command.run(invalid_image_timeout, None) == 2
    collision = parser.parse_args([*common, "--run-manifest", str(existing)])
    assert command.run(collision, None) == 2
    assert remote_calls == 0
    assert not (tmp_path / "invalid-timeout.json").exists()
    assert existing.read_text(encoding="utf-8") == "owner"


def test_finalizer_never_overwrites_another_reservation(tmp_path: Path) -> None:
    output = tmp_path / "owned.json"
    first = {"owner": "first"}
    replacement = {"owner": "replacement"}
    reserve_json_output(output, first)
    atomic_write_json(output, replacement)

    with pytest.raises(ParseBenchGatewayError) as captured:
        finalize_json_output(
            output,
            reservation=first,
            value={"status": "finished"},
        )

    assert captured.value.code == "output_ownership_lost"
    assert json.loads(output.read_text(encoding="utf-8")) == replacement


def test_reserver_never_removes_an_identical_existing_output(tmp_path: Path) -> None:
    output = tmp_path / "preexisting.json"
    reservation = {"owner": "existing"}
    atomic_write_json(output, reservation)
    original_inode = output.stat().st_ino

    with pytest.raises(ParseBenchGatewayError) as captured:
        reserve_json_output(output, reservation)

    assert captured.value.code == "output_exists"
    assert output.stat().st_ino == original_inode
    assert json.loads(output.read_text(encoding="utf-8")) == reservation


def test_report_failure_releases_its_reservation_for_retry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parser, command = _parser_and_command()
    run_path, manifest = _run_manifest(tmp_path)
    summary = {
        "batch_id": manifest.batch_id,
        "status": "done",
        "total": 1,
        "completed": 1,
        "failed": 0,
    }

    class FakeGatewayClient:
        def __init__(self, *_args, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def batch_status(self, *_args, **_kwargs):
            return summary

        def batch_results(self, *_args, **_kwargs):
            return {**summary, "results": []}

    monkeypatch.setattr(
        "aworld_cli.top_level_commands.benchmark_cmd.ParseBenchGatewayClient",
        FakeGatewayClient,
    )
    monkeypatch.setattr(
        "aworld_cli.top_level_commands.benchmark_cmd.reduce_gateway_batch_results",
        lambda *_args: (_ for _ in ()).throw(
            ParseBenchGatewayError("reward_invalid", "invalid reward")
        ),
    )
    report_path = tmp_path / "retry-report.json"
    args = parser.parse_args(
        [
            "benchmark",
            "parsebench",
            "report",
            "--run-manifest",
            str(run_path),
            "--output",
            str(report_path),
            "--gateway-url",
            "https://gateway.example.test",
        ]
    )

    assert command.run(args, None) == 2
    assert not report_path.exists()
    monkeypatch.setattr(
        "aworld_cli.top_level_commands.benchmark_cmd.reduce_gateway_batch_results",
        lambda *_args: {
            "schema": "aworld.parsebench.gateway-report/v2",
            "batch_id": manifest.batch_id,
            "status": "scored",
            "publishable": False,
            "overall_score": 0.5,
        },
    )
    assert command.run(args, None) == 0
    assert json.loads(report_path.read_text(encoding="utf-8"))["overall_score"] == 0.5


def test_status_rejects_a_different_gateway_before_query(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parser, command = _parser_and_command()
    run_path, _manifest = _run_manifest(tmp_path)
    client_calls = 0

    class ForbiddenGatewayClient:
        def __init__(self, *_args, **_kwargs):
            nonlocal client_calls
            client_calls += 1

    monkeypatch.setattr(
        "aworld_cli.top_level_commands.benchmark_cmd.ParseBenchGatewayClient",
        ForbiddenGatewayClient,
    )
    args = parser.parse_args(
        [
            "benchmark",
            "parsebench",
            "status",
            "--run-manifest",
            str(run_path),
            "--gateway-url",
            "https://other-gateway.example.test",
        ]
    )

    assert command.run(args, None) == 2
    assert client_calls == 0


def test_report_waits_for_terminal_status_without_fetching_results(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parser, command = _parser_and_command()
    run_path, manifest = _run_manifest(tmp_path)
    result_calls = 0

    class FakeGatewayClient:
        def __init__(self, *_args, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def batch_status(self, *_args, **_kwargs):
            return {
                "batch_id": manifest.batch_id,
                "status": "running",
                "total": 1,
                "completed": 0,
                "failed": 0,
            }

        def batch_results(self, *_args, **_kwargs):
            nonlocal result_calls
            result_calls += 1
            raise AssertionError("non-terminal results must not be fetched")

    monkeypatch.setattr(
        "aworld_cli.top_level_commands.benchmark_cmd.ParseBenchGatewayClient",
        FakeGatewayClient,
    )
    report_path = tmp_path / "nonterminal-report.json"
    args = parser.parse_args(
        [
            "benchmark",
            "parsebench",
            "report",
            "--run-manifest",
            str(run_path),
            "--output",
            str(report_path),
            "--gateway-url",
            "https://gateway.example.test",
        ]
    )

    assert command.run(args, None) == 2
    assert result_calls == 0
    assert not report_path.exists()
