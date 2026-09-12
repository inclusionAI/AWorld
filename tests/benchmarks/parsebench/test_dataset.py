from __future__ import annotations

from dataclasses import replace
from hashlib import sha1, sha256
from io import BytesIO
import json
import os
from pathlib import Path
import subprocess
import tarfile
import tomllib
from zipfile import ZipFile

import pytest

from aworld.benchmarks.parsebench.contracts import (
    PARSEBENCH_TASK_FILENAME,
    PARSEBENCH_TASK_RUNTIME_PATH,
    PARSEBENCH_TASK_SCHEMA_VERSION,
    PARSEBENCH_SCOPE_FILENAME,
    PARSEBENCH_SCOPE_RUNTIME_PATH,
    PARSEBENCH_SCOPE_SCHEMA_VERSION,
    PINNED_PARSEBENCH_CONTRACT,
    ParseBenchDatasetError,
    ParseBenchDimension,
    ParseBenchExecution,
    ParseBenchSourceDataset,
    SmokeSelection,
)
from aworld.benchmarks.parsebench.dataset import (
    _package_scope,
    build_parsebench_executable_dataset,
    load_parsebench_checkout,
    select_smoke_executions,
    task_id_for_source,
)


_PRIVATE_TEXT = "PRIVATE_EXPECTATION_DO_NOT_EXPOSE"


def _row(
    *,
    pdf: str,
    category: str,
    rule_id: str,
    rule_type: str,
    rule: dict[str, object] | None = None,
    page: int | None = None,
    expected_markdown: str | None = None,
    tags: list[str] | None = None,
) -> dict[str, object]:
    return {
        "pdf": pdf,
        "category": category,
        "id": rule_id,
        "type": rule_type,
        "rule": json.dumps(rule or {}, sort_keys=True, separators=(",", ":")),
        "page": page,
        "expected_markdown": expected_markdown,
        "tags": tags or [],
    }


def _write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    path.write_text(
        "".join(
            json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            + "\n"
            for row in rows
        ),
        encoding="utf-8",
    )


def _blob_id(content: bytes, *, git_blob: bool) -> str:
    if not git_blob:
        return sha256(content).hexdigest()
    digest = sha1(
        f"blob {len(content)}\0".encode("ascii"),
        usedforsecurity=False,
    )
    digest.update(content)
    return digest.hexdigest()


def _fixture_material_digests(source_root: Path) -> tuple[tuple[str, int, str], ...]:
    logical_paths = {
        relative_path
        for _dimension, relative_path in PINNED_PARSEBENCH_CONTRACT.source_files
    }
    for _dimension, relative_path in PINNED_PARSEBENCH_CONTRACT.source_files:
        for line in (
            (source_root / relative_path).read_text(encoding="utf-8").splitlines()
        ):
            if line:
                logical_paths.add(json.loads(line)["pdf"])
    return tuple(
        (
            relative_path,
            (source_root / relative_path).stat().st_size,
            "sha256:" + sha256((source_root / relative_path).read_bytes()).hexdigest(),
        )
        for relative_path in sorted(logical_paths)
    )


def _fixture_contract(source_root: Path, *, unique_executions: int = 4):
    one_each = tuple(
        (dimension, 1) for dimension, _ in PINNED_PARSEBENCH_CONTRACT.source_files
    )
    return replace(
        PINNED_PARSEBENCH_CONTRACT,
        rule_counts=one_each,
        dimension_execution_counts=one_each,
        unique_execution_count=unique_executions,
        material_digests=_fixture_material_digests(source_root),
    )


def _write_export_fixture(tmp_path: Path) -> Path:
    root = tmp_path / "parsebench-checkout"
    (root / "docs" / "chart").mkdir(parents=True)
    (root / "docs" / "layout").mkdir(parents=True)
    (root / "docs" / "table").mkdir(parents=True)
    (root / "docs" / "text").mkdir(parents=True)
    resources = {
        "docs/chart/chart.pdf": b"%PDF-1.4\nsynthetic chart\n%%EOF\n",
        "docs/layout/layout.png": b"synthetic png bytes",
        "docs/table/table.pdf": b"%PDF-1.4\nsynthetic table\n%%EOF\n",
        "docs/text/shared.pdf": b"%PDF-1.4\nsynthetic shared text\n%%EOF\n",
    }
    for relative, content in resources.items():
        (root / relative).write_bytes(content)

    _write_jsonl(
        root / "chart.jsonl",
        [
            _row(
                pdf="docs/chart/chart.pdf",
                category="chart",
                rule_id="chart-rule",
                rule_type="chart_data_point",
                rule={"labels": ["Revenue"], "value": _PRIVATE_TEXT},
                tags=["need_estimate"],
            )
        ],
    )
    _write_jsonl(
        root / "layout.jsonl",
        [
            _row(
                pdf="docs/layout/layout.png",
                category="layout",
                rule_id="layout-rule",
                rule_type="layout",
                rule={
                    "bbox": [0.1, 0.2, 0.3, 0.4],
                    "canonical_class": "Text",
                    "content": {"type": "text", "text": _PRIVATE_TEXT},
                },
                page=1,
                tags=["hard"],
            )
        ],
    )
    _write_jsonl(
        root / "table.jsonl",
        [
            _row(
                pdf="docs/table/table.pdf",
                category="table",
                rule_id="table-rule",
                rule_type="expected_markdown",
                expected_markdown=f"<table><tr><td>{_PRIVATE_TEXT}</td></tr></table>",
                tags=["hard"],
            )
        ],
    )
    _write_jsonl(
        root / "text_content.jsonl",
        [
            _row(
                pdf="docs/text/shared.pdf",
                category="text_content",
                rule_id="content-rule",
                rule_type="missing_specific_word",
                rule={"word": _PRIVATE_TEXT},
                tags=["dense"],
            )
        ],
    )
    _write_jsonl(
        root / "text_formatting.jsonl",
        [
            _row(
                pdf="docs/text/shared.pdf",
                category="text_formatting",
                rule_id="formatting-rule",
                rule_type="is_bold",
                rule={"text": _PRIVATE_TEXT},
                tags=["dense"],
            )
        ],
    )
    return root


def _load_fixture(tmp_path: Path):
    root = _write_fixture(tmp_path)
    return root, load_parsebench_checkout(root, contract=_fixture_contract(root))


def _write_hf_snapshot_fixture(tmp_path: Path) -> Path:
    exported = _write_export_fixture(tmp_path)
    cache_root = tmp_path / "datasets--llamaindex--ParseBench"
    blobs_root = cache_root / "blobs"
    snapshot = cache_root / "snapshots" / PINNED_PARSEBENCH_CONTRACT.dataset_revision
    blobs_root.mkdir(parents=True)
    for source in sorted(path for path in exported.rglob("*") if path.is_file()):
        content = source.read_bytes()
        blob = blobs_root / _blob_id(content, git_blob=source.suffix == ".jsonl")
        blob.write_bytes(content)
        destination = snapshot / source.relative_to(exported)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.symlink_to(os.path.relpath(blob, destination.parent))
    return snapshot


def _write_fixture(tmp_path: Path) -> Path:
    return _write_hf_snapshot_fixture(tmp_path)


def _readdress_snapshot_file(path: Path) -> None:
    content = path.read_bytes()
    blobs_root = next(parent for parent in path.parents if parent.name == "snapshots")
    blobs_root = blobs_root.parent / "blobs"
    blob = blobs_root / _blob_id(content, git_blob=path.suffix == ".jsonl")
    blob.write_bytes(content)
    path.unlink()
    path.symlink_to(os.path.relpath(blob, path.parent))


def _run_git(root: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(root), *arguments],
        check=True,
        capture_output=True,
        text=True,
    )


def _write_git_fixture(tmp_path: Path, *, track_resources: bool = True):
    root = _write_export_fixture(tmp_path)
    contract = _fixture_contract(root)
    _run_git(root, "init", "--quiet")
    _run_git(root, "config", "user.email", "parsebench-fixture@example.invalid")
    _run_git(root, "config", "user.name", "ParseBench Fixture")
    _run_git(root, "add", "*.jsonl")
    if track_resources:
        _run_git(root, "add", "docs")
    _run_git(root, "commit", "--quiet", "-m", "fixture")
    revision = _run_git(root, "rev-parse", "HEAD").stdout.strip()
    return root, replace(contract, dataset_revision=revision)


def test_loader_validates_decodes_and_groups_shared_parse_execution(
    tmp_path: Path,
) -> None:
    root, dataset = _load_fixture(tmp_path)

    assert dataset.dataset_revision == PINNED_PARSEBENCH_CONTRACT.dataset_revision
    assert dataset.scorer_revision == PINNED_PARSEBENCH_CONTRACT.scorer_revision
    assert dataset.revision_evidence == "huggingface-content-addressed-snapshot"
    assert len(dataset.rules) == 5
    assert len(dataset.executions) == 4
    shared = next(
        item
        for item in dataset.executions
        if item.source_path == "docs/text/shared.pdf"
    )
    assert shared.dimensions == (
        ParseBenchDimension.TEXT_CONTENT,
        ParseBenchDimension.TEXT_FORMATTING,
    )
    assert [rule.rule_id for rule in shared.rules] == [
        "content-rule",
        "formatting-rule",
    ]
    assert shared.rules[0].rule_payload == {"word": _PRIVATE_TEXT}
    assert shared.rules[0].source_jsonl == "text_content.jsonl"
    assert shared.rules[0].source_line == 1
    assert (
        shared.source_sha256
        == "sha256:" + sha256((root / shared.source_path).read_bytes()).hexdigest()
    )
    assert {item.relative_path for item in dataset.source_files} == {
        "chart.jsonl",
        "layout.jsonl",
        "table.jsonl",
        "text_content.jsonl",
        "text_formatting.jsonl",
    }


def test_loader_accepts_materialized_hugging_face_snapshot_symlinks(
    tmp_path: Path,
) -> None:
    snapshot = _write_hf_snapshot_fixture(tmp_path)

    dataset = load_parsebench_checkout(snapshot, contract=_fixture_contract(snapshot))

    assert dataset.source_root == snapshot
    assert dataset.revision_evidence == "huggingface-content-addressed-snapshot"
    assert len(dataset.executions) == 4


def test_loader_rejects_loose_files_in_revision_named_hf_snapshot(
    tmp_path: Path,
) -> None:
    exported = _write_export_fixture(tmp_path)
    contract = _fixture_contract(exported)
    cache_root = tmp_path / "datasets--llamaindex--ParseBench"
    snapshot = cache_root / "snapshots" / PINNED_PARSEBENCH_CONTRACT.dataset_revision
    snapshot.parent.mkdir(parents=True)
    exported.rename(snapshot)
    (cache_root / "blobs").mkdir()

    with pytest.raises(ParseBenchDatasetError) as captured:
        load_parsebench_checkout(snapshot, contract=contract)

    assert captured.value.code == "source_revision_unverifiable"


def test_loader_accepts_clean_git_checkout_with_all_material_tracked(
    tmp_path: Path,
) -> None:
    root, contract = _write_git_fixture(tmp_path)

    dataset = load_parsebench_checkout(root, contract=contract)

    assert dataset.dataset_revision == contract.dataset_revision
    assert dataset.revision_evidence == "clean-git-checkout"


def test_loader_groups_the_same_document_by_one_indexed_execution_page(
    tmp_path: Path,
) -> None:
    root = _write_fixture(tmp_path)
    layout_path = root / "layout.jsonl"
    rows = [json.loads(line) for line in layout_path.read_text().splitlines()]
    second = dict(rows[0])
    second["id"] = "layout-rule-page-two"
    second["page"] = 2
    _write_jsonl(layout_path, [rows[0], second])
    _readdress_snapshot_file(layout_path)
    contract = replace(
        _fixture_contract(root),
        rule_counts=tuple(
            (dimension, 2 if dimension is ParseBenchDimension.LAYOUT else count)
            for dimension, count in _fixture_contract(root).rule_counts
        ),
        dimension_execution_counts=tuple(
            (dimension, 2 if dimension is ParseBenchDimension.LAYOUT else count)
            for dimension, count in _fixture_contract(root).dimension_execution_counts
        ),
        unique_execution_count=5,
    )

    dataset = load_parsebench_checkout(root, contract=contract)

    layout_executions = [
        item
        for item in dataset.executions
        if ParseBenchDimension.LAYOUT in item.dimensions
    ]
    assert {item.page for item in layout_executions} == {1, 2}
    assert len({item.task_id for item in layout_executions}) == 2
    assert {item.page: item.task_id for item in layout_executions} == {
        1: task_id_for_source("docs/layout/layout.png", page=1),
        2: task_id_for_source("docs/layout/layout.png", page=2),
    }


@pytest.mark.parametrize(
    ("mutation", "code"),
    (
        ("bad_rule_json", "rule_json_invalid"),
        ("missing_field", "source_schema_mismatch"),
        ("extra_field", "source_schema_mismatch"),
        ("missing_resource", "resource_missing"),
        ("path_traversal", "resource_path_unsafe"),
        ("external_symlink", "resource_path_unsafe"),
        ("duplicate_rule_id", "duplicate_rule_id"),
    ),
)
def test_loader_rejects_invalid_source_contract(
    tmp_path: Path, mutation: str, code: str
) -> None:
    root = _write_fixture(tmp_path)
    contract = _fixture_contract(root)
    rows = [
        json.loads(line) for line in (root / "chart.jsonl").read_text().splitlines()
    ]
    if mutation == "bad_rule_json":
        rows[0]["rule"] = "{not-json"
    elif mutation == "missing_field":
        rows[0].pop("tags")
    elif mutation == "extra_field":
        rows[0]["verified"] = True
    elif mutation == "missing_resource":
        rows[0]["pdf"] = "docs/chart/missing.pdf"
    elif mutation == "path_traversal":
        (root.parent / "escape.pdf").write_bytes(b"outside")
        rows[0]["pdf"] = "../escape.pdf"
    elif mutation == "external_symlink":
        outside = root.parent / "escape.pdf"
        outside.write_bytes(b"outside")
        resource = root / "docs/chart/chart.pdf"
        resource.unlink()
        resource.symlink_to(outside)
    elif mutation == "duplicate_rule_id":
        rows[0]["id"] = "table-rule"
    _write_jsonl(root / "chart.jsonl", rows)

    with pytest.raises(ParseBenchDatasetError) as captured:
        load_parsebench_checkout(root, contract=contract)

    assert captured.value.code == code
    assert not hasattr(captured.value, "row")


def test_loader_requires_pinned_revision_and_full_cardinality(tmp_path: Path) -> None:
    root = _write_fixture(tmp_path)
    contract = _fixture_contract(root)
    with pytest.raises(ParseBenchDatasetError) as revision_error:
        load_parsebench_checkout(
            root,
            contract=replace(contract, dataset_revision="0" * 40),
        )
    assert revision_error.value.code == "source_revision_mismatch"

    with pytest.raises(ParseBenchDatasetError) as cardinality_error:
        load_parsebench_checkout(
            root,
            contract=replace(contract, unique_execution_count=5),
        )
    assert cardinality_error.value.code == "cardinality_mismatch"


def test_loader_rejects_forgeable_export_revision_marker(tmp_path: Path) -> None:
    root = _write_export_fixture(tmp_path)
    contract = _fixture_contract(root)
    (root / ".parsebench-revision").write_text(
        PINNED_PARSEBENCH_CONTRACT.dataset_revision + "\n",
        encoding="ascii",
    )

    with pytest.raises(ParseBenchDatasetError) as captured:
        load_parsebench_checkout(root, contract=contract)

    assert captured.value.code == "source_revision_marker_unsupported"


@pytest.mark.parametrize("relative_path", ("chart.jsonl", "docs/chart/chart.pdf"))
def test_loader_rejects_same_cardinality_hf_blob_tampering(
    tmp_path: Path,
    relative_path: str,
) -> None:
    root = _write_fixture(tmp_path)
    contract = _fixture_contract(root)
    target = root / relative_path
    if target.suffix == ".jsonl":
        rows = [json.loads(line) for line in target.read_text().splitlines()]
        rows[0]["rule"] = json.dumps({"labels": ["Revenue"], "value": "TAMPERED"})
        _write_jsonl(target, rows)
    else:
        target.write_bytes(target.read_bytes() + b"tampered")

    with pytest.raises(ParseBenchDatasetError) as captured:
        load_parsebench_checkout(root, contract=contract)

    assert captured.value.code == "source_content_mismatch"


@pytest.mark.parametrize("relative_path", ("chart.jsonl", "docs/chart/chart.pdf"))
def test_loader_rejects_readdressed_hf_material_with_self_consistent_blob(
    tmp_path: Path,
    relative_path: str,
) -> None:
    root = _write_fixture(tmp_path)
    contract = _fixture_contract(root)
    target = root / relative_path
    if target.suffix == ".jsonl":
        rows = [json.loads(line) for line in target.read_text().splitlines()]
        rows[0]["rule"] = json.dumps({"labels": ["Revenue"], "value": "RE-ADDRESSED"})
        _write_jsonl(target, rows)
    else:
        target.write_bytes(target.read_bytes() + b"re-addressed")
    _readdress_snapshot_file(target)

    with pytest.raises(ParseBenchDatasetError) as captured:
        load_parsebench_checkout(root, contract=contract)

    assert captured.value.code == "source_content_mismatch"


@pytest.mark.parametrize("relative_path", ("chart.jsonl", "docs/chart/chart.pdf"))
def test_loader_rejects_same_cardinality_git_checkout_tampering(
    tmp_path: Path,
    relative_path: str,
) -> None:
    root, contract = _write_git_fixture(tmp_path)
    target = root / relative_path
    if target.suffix == ".jsonl":
        rows = [json.loads(line) for line in target.read_text().splitlines()]
        rows[0]["rule"] = json.dumps({"labels": ["Revenue"], "value": "TAMPERED"})
        _write_jsonl(target, rows)
    else:
        target.write_bytes(target.read_bytes() + b"tampered")

    with pytest.raises(ParseBenchDatasetError) as captured:
        load_parsebench_checkout(root, contract=contract)

    assert captured.value.code == "source_revision_dirty"


def test_loader_rejects_untracked_git_source_material(tmp_path: Path) -> None:
    root, contract = _write_git_fixture(tmp_path, track_resources=False)

    with pytest.raises(ParseBenchDatasetError) as captured:
        load_parsebench_checkout(root, contract=contract)

    assert captured.value.code == "source_revision_dirty"


@pytest.mark.parametrize("dirty_target", (False, True))
def test_loader_rejects_tracked_git_material_symlink_and_dirty_target(
    tmp_path: Path,
    dirty_target: bool,
) -> None:
    root = _write_export_fixture(tmp_path)
    source = root / "docs/chart/chart.pdf"
    target = root / "docs/chart/tracked-target.pdf"
    target.write_bytes(source.read_bytes())
    source.unlink()
    source.symlink_to(target.name)
    contract = _fixture_contract(root)
    _run_git(root, "init", "--quiet")
    _run_git(root, "config", "user.email", "parsebench-fixture@example.invalid")
    _run_git(root, "config", "user.name", "ParseBench Fixture")
    _run_git(root, "add", ".")
    _run_git(root, "commit", "--quiet", "-m", "symlink fixture")
    revision = _run_git(root, "rev-parse", "HEAD").stdout.strip()
    contract = replace(contract, dataset_revision=revision)
    if dirty_target:
        target.write_bytes(target.read_bytes() + b"dirty")

    with pytest.raises(ParseBenchDatasetError) as captured:
        load_parsebench_checkout(root, contract=contract)

    assert captured.value.code == "source_revision_dirty"


@pytest.mark.parametrize(
    "index_flag",
    ("--assume-unchanged", "--skip-worktree"),
)
def test_loader_rejects_git_index_flags_that_can_hide_material_changes(
    tmp_path: Path,
    index_flag: str,
) -> None:
    root, contract = _write_git_fixture(tmp_path)
    target = root / "chart.jsonl"
    _run_git(root, "update-index", index_flag, "chart.jsonl")
    rows = [json.loads(line) for line in target.read_text().splitlines()]
    rows[0]["rule"] = json.dumps({"labels": ["Revenue"], "value": "TAMPERED"})
    _write_jsonl(target, rows)

    with pytest.raises(ParseBenchDatasetError) as captured:
        load_parsebench_checkout(root, contract=contract)

    assert captured.value.code == "source_revision_dirty"


def test_loader_requires_every_dimension_file(tmp_path: Path) -> None:
    root = _write_fixture(tmp_path)
    contract = _fixture_contract(root)
    (root / "layout.jsonl").unlink()

    with pytest.raises(ParseBenchDatasetError) as captured:
        load_parsebench_checkout(root, contract=contract)

    assert captured.value.code == "source_file_missing"


def test_loader_rejects_dimension_jsonl_symlink_outside_checkout(
    tmp_path: Path,
) -> None:
    root = _write_fixture(tmp_path)
    contract = _fixture_contract(root)
    source = root / "chart.jsonl"
    outside = root.parent / "chart.jsonl"
    outside.write_bytes(source.read_bytes())
    source.unlink()
    source.symlink_to(outside)

    with pytest.raises(ParseBenchDatasetError) as captured:
        load_parsebench_checkout(root, contract=contract)

    assert captured.value.code == "source_file_unsafe"


def test_loader_fails_closed_on_deterministic_task_id_collision(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _write_fixture(tmp_path)
    contract = _fixture_contract(root)
    monkeypatch.setattr(
        "aworld.benchmarks.parsebench.dataset.task_id_for_source",
        lambda _source_path, *, page=None: "pb-" + "0" * 32,
    )

    with pytest.raises(ParseBenchDatasetError) as captured:
        load_parsebench_checkout(root, contract=contract)

    assert captured.value.code == "task_id_collision"


def test_smoke_selection_is_order_independent_and_covers_every_dimension(
    tmp_path: Path,
) -> None:
    _, dataset = _load_fixture(tmp_path)
    selection = SmokeSelection(per_dimension=1, seed="fixture-smoke-v1")

    first = select_smoke_executions(dataset.executions, selection)
    second = select_smoke_executions(tuple(reversed(dataset.executions)), selection)

    assert [item.task_id for item in first] == [item.task_id for item in second]
    covered = {dimension for item in first for dimension in item.dimensions}
    assert covered == set(ParseBenchDimension)


def test_smoke_selection_reduces_each_dimension_by_stable_hash() -> None:
    executions = tuple(
        ParseBenchExecution(
            task_id=f"pb-{dimension.value}-{ordinal}",
            source_path=f"docs/{dimension.value}/{ordinal}.pdf",
            source_file=Path(f"/{dimension.value}/{ordinal}.pdf"),
            source_size=1,
            source_sha256="sha256:" + "0" * 64,
            page=None,
            dimensions=(dimension,),
            rules=(),
        )
        for dimension in ParseBenchDimension
        for ordinal in range(3)
    )
    selection = SmokeSelection(per_dimension=1, seed="stable-v1")

    chosen = select_smoke_executions(executions, selection)
    chosen_again = select_smoke_executions(tuple(reversed(executions)), selection)

    assert [item.task_id for item in chosen] == [item.task_id for item in chosen_again]
    assert len(chosen) == 5
    assert {item.dimensions[0] for item in chosen} == set(ParseBenchDimension)


def test_converter_rejects_mutable_runtime_image_without_explicit_local_opt_in(
    tmp_path: Path,
) -> None:
    root = _write_fixture(tmp_path)

    with pytest.raises(ParseBenchDatasetError) as captured:
        build_parsebench_executable_dataset(
            root,
            tmp_path / "parsebench.zip",
            contract=_fixture_contract(root),
            selection=SmokeSelection(per_dimension=1),
            runtime_image="aworld-filex-parsebench:latest",
        )

    assert captured.value.code == "runtime_image_mutable"


def test_smoke_package_cannot_reuse_official_dataset_identity(tmp_path: Path) -> None:
    root = _write_fixture(tmp_path)

    with pytest.raises(ParseBenchDatasetError) as captured:
        build_parsebench_executable_dataset(
            root,
            tmp_path / "parsebench.zip",
            contract=_fixture_contract(root),
            selection=SmokeSelection(per_dimension=1),
            dataset_id="parsebench-2805a1d9",
            runtime_image="registry.example/parsebench@sha256:" + "1" * 64,
        )

    assert captured.value.code == "dataset_id_scope_mismatch"


def test_smoke_default_dataset_identity_is_scope_specific(tmp_path: Path) -> None:
    root = _write_fixture(tmp_path)
    output = tmp_path / "parsebench.zip"

    result = build_parsebench_executable_dataset(
        root,
        output,
        contract=_fixture_contract(root),
        selection=SmokeSelection(per_dimension=1, seed="identity-v1"),
        runtime_image="registry.example/parsebench@sha256:" + "2" * 64,
    )

    assert result.publishable is False
    with ZipFile(output) as package:
        descriptor = package.read("dataset.yaml").decode("utf-8")
        assert 'dataset_id: "parsebench-2805a1d9-smoke-' in descriptor
        assert (
            'non_publishable_reasons: ["smoke_selection", "noncanonical_contract"]'
            in descriptor
        )


def test_only_complete_pinned_contract_can_be_publishable() -> None:
    memberships = [set() for _ in range(2_078)]
    assignments = {
        ParseBenchDimension.CHART: range(0, 568),
        ParseBenchDimension.LAYOUT: range(568, 1_068),
        ParseBenchDimension.TABLE: range(1_068, 1_571),
        ParseBenchDimension.TEXT_CONTENT: range(1_571, 2_077),
        ParseBenchDimension.TEXT_FORMATTING: (*range(0, 475), 2_077),
    }
    for dimension, ordinals in assignments.items():
        for ordinal in ordinals:
            memberships[ordinal].add(dimension)
    executions = tuple(
        ParseBenchExecution(
            task_id=f"pb-{ordinal:032x}",
            source_path=f"docs/full/{ordinal}.pdf",
            source_file=Path(f"/full/{ordinal}.pdf"),
            source_size=1,
            source_sha256="sha256:" + f"{ordinal:064x}",
            page=None,
            dimensions=tuple(
                dimension
                for dimension in ParseBenchDimension
                if dimension in memberships[ordinal]
            ),
            rules=(),
        )
        for ordinal in range(2_078)
    )
    dataset = ParseBenchSourceDataset(
        source_root=Path("/pinned"),
        dataset_revision=PINNED_PARSEBENCH_CONTRACT.dataset_revision,
        scorer_revision=PINNED_PARSEBENCH_CONTRACT.scorer_revision,
        revision_evidence="test",
        source_files=(),
        rules=(),
        executions=executions,
    )
    immutable_image = "registry.example/parsebench@sha256:" + "3" * 64

    official = _package_scope(
        dataset,
        executions,
        contract=PINNED_PARSEBENCH_CONTRACT,
        selection=None,
        runtime_image=immutable_image,
        immutable_runtime_image=True,
    )
    custom = _package_scope(
        dataset,
        executions,
        contract=replace(
            PINNED_PARSEBENCH_CONTRACT,
            unique_execution_count=2_077,
        ),
        selection=None,
        runtime_image=immutable_image,
        immutable_runtime_image=True,
    )

    assert official.publishable is True
    assert official.kind == "official-full"
    assert official.non_publishable_reasons == ()
    assert custom.publishable is False
    assert custom.kind == "custom"
    assert custom.non_publishable_reasons == ("noncanonical_contract",)


def test_converter_builds_deterministic_private_harbor_package(tmp_path: Path) -> None:
    root = _write_fixture(tmp_path)
    output_one = tmp_path / "one" / "parsebench.zip"
    output_two = tmp_path / "two" / "parsebench.zip"
    kwargs = {
        "contract": _fixture_contract(root),
        "selection": SmokeSelection(per_dimension=1, seed="fixture-smoke-v1"),
        "dataset_id": "yes",
        "service_name": "null",
        "runtime_image": "aworld-filex-parsebench:test",
        "allow_mutable_local_image": True,
    }

    result_one = build_parsebench_executable_dataset(root, output_one, **kwargs)
    result_two = build_parsebench_executable_dataset(root, output_two, **kwargs)

    assert output_one.read_bytes() == output_two.read_bytes()
    assert result_one.package_sha256 == result_two.package_sha256
    assert result_one.task_count == 4
    assert result_one.rule_count == 5
    assert result_one.dimensions == tuple(ParseBenchDimension)
    assert result_one.publishable is False
    assert result_one.selection_manifest_sha256.startswith("sha256:")

    with ZipFile(output_one) as package:
        names = set(package.namelist())
        assert {"dataset.yaml", "dataset.jsonl", "manifest.json", "README.md"} <= names
        descriptor = package.read("dataset.yaml").decode()
        assert 'dataset_id: "yes"' in descriptor
        assert 'service_name: "null"' in descriptor
        assert "publishable: false" in descriptor
        assert 'agent_network_mode: "no-network"' in descriptor
        task_names = sorted(name for name in names if name.startswith("tasks/"))
        assert len(task_names) == 4
        catalog_bytes = package.read("dataset.jsonl")
        catalog = [json.loads(line) for line in catalog_bytes.splitlines()]
        material_manifest = json.loads(package.read("manifest.json"))
        assert (
            material_manifest["schema_version"] == "yolo-dataset-material-manifest/v1"
        )
        assert material_manifest["catalog"] == {
            "path": "dataset.jsonl",
            "size": len(catalog_bytes),
            "sha256": "sha256:" + sha256(catalog_bytes).hexdigest(),
        }
        selection_manifest = material_manifest["selection_manifest"]
        assert (
            "sha256:"
            + sha256(
                json.dumps(
                    selection_manifest,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest()
            == result_one.selection_manifest_sha256
        )
        assert {case["task_id"] for case in selection_manifest["cases"]} == {
            row["task_id"] for row in catalog
        }
        assert set(selection_manifest["dimension_task_ids"]) == {
            dimension.value for dimension in ParseBenchDimension
        }
        assert not any(
            forbidden in json.dumps(selection_manifest)
            for forbidden in ("rules", "tags", "expected_markdown")
        )
        assert (
            material_manifest["benchmark_scope"]
            == material_manifest["provenance"]["benchmark_scope"]
        )
        provenance = material_manifest["provenance"]
        assert (
            provenance["dataset_revision"]
            == PINNED_PARSEBENCH_CONTRACT.dataset_revision
        )
        assert (
            provenance["scorer_revision"] == PINNED_PARSEBENCH_CONTRACT.scorer_revision
        )
        assert provenance["source_rule_count"] == 5
        assert provenance["source_execution_count"] == 4
        assert provenance["selected_rule_count"] == 5
        assert provenance["selected_execution_count"] == 4
        assert provenance["runtime_image"] == "aworld-filex-parsebench:test"
        assert provenance["benchmark_scope"]["publishable"] is False
        assert provenance["benchmark_scope"]["non_publishable_reasons"] == [
            "smoke_selection",
            "noncanonical_contract",
            "mutable_runtime_image",
        ]
        assert (
            provenance["benchmark_scope"]["selection_manifest_sha256"]
            == result_one.selection_manifest_sha256
        )
        assert provenance["revision_evidence"] == (
            "huggingface-content-addressed-snapshot"
        )
        assert provenance["task_contract"] == {
            "filename": PARSEBENCH_TASK_FILENAME,
            "runtime_path": PARSEBENCH_TASK_RUNTIME_PATH,
            "schema_version": PARSEBENCH_TASK_SCHEMA_VERSION,
        }
        assert len(provenance["source_files"]) == 5

        for row in catalog:
            assert row["task_dir"] == f"tasks/{row['task_id']}.tar.gz"
            assert row["source"].endswith("/" + row["task_id"])
            assert "source_path" not in row
            assert "dimensions" not in row
            assert "rule_count" not in row
            assert "rules" not in row
            assert "rule_ids" not in row
            assert "expected_markdown" not in row
            assert "tags" not in row
            assert _PRIVATE_TEXT not in json.dumps(row)
            assert row["task_contract"] == {
                "path": f"environment/{PARSEBENCH_TASK_FILENAME}",
                "runtime_path": PARSEBENCH_TASK_RUNTIME_PATH,
                "schema_version": PARSEBENCH_TASK_SCHEMA_VERSION,
            }

            archive_bytes = package.read(row["task_dir"])
            declared = next(
                item
                for item in material_manifest["tasks"]
                if item["task_id"] == row["task_id"]
            )
            assert declared["size"] == len(archive_bytes)
            assert declared["sha256"] == "sha256:" + sha256(archive_bytes).hexdigest()
            with tarfile.open(fileobj=BytesIO(archive_bytes), mode="r:gz") as task:
                members = {item.name for item in task.getmembers() if item.isfile()}
                prefix = row["task_id"]
                assert f"{prefix}/task.toml" in members
                assert f"{prefix}/instruction.md" in members
                assert f"{prefix}/environment/Dockerfile" in members
                assert f"{prefix}/environment/{PARSEBENCH_TASK_FILENAME}" in members
                assert f"{prefix}/environment/{PARSEBENCH_SCOPE_FILENAME}" in members
                environment_source = next(
                    name
                    for name in members
                    if name.startswith(f"{prefix}/environment/input/document.")
                )
                verifier_source = environment_source.replace(
                    f"{prefix}/environment/input/", f"{prefix}/tests/input/"
                )
                assert verifier_source in members
                assert f"{prefix}/tests/Dockerfile" in members
                assert f"{prefix}/tests/{PARSEBENCH_SCOPE_FILENAME}" in members
                assert f"{prefix}/tests/test.sh" in members
                assert f"{prefix}/tests/ground_truth.json" in members
                environment_files = {
                    name
                    for name in members
                    if name.startswith(f"{prefix}/environment/")
                }
                assert not any("ground_truth" in name for name in environment_files)
                instruction = (
                    task.extractfile(f"{prefix}/instruction.md").read().decode()
                )
                dockerfile = (
                    task.extractfile(f"{prefix}/environment/Dockerfile").read().decode()
                )
                verifier_dockerfile = (
                    task.extractfile(f"{prefix}/tests/Dockerfile").read().decode()
                )
                verifier_script = (
                    task.extractfile(f"{prefix}/tests/test.sh").read().decode()
                )
                environment_source_bytes = task.extractfile(environment_source).read()
                verifier_source_bytes = task.extractfile(verifier_source).read()
                task_toml_text = task.extractfile(f"{prefix}/task.toml").read().decode()
                task_config = tomllib.loads(task_toml_text)
                assert task_config["schema_version"] == "1.4"
                assert "version" not in task_config
                public_task = json.load(
                    task.extractfile(f"{prefix}/environment/{PARSEBENCH_TASK_FILENAME}")
                )
                public_scope = json.load(
                    task.extractfile(
                        f"{prefix}/environment/{PARSEBENCH_SCOPE_FILENAME}"
                    )
                )
                verifier_scope = json.load(
                    task.extractfile(f"{prefix}/tests/{PARSEBENCH_SCOPE_FILENAME}")
                )
                ground_truth = json.load(
                    task.extractfile(f"{prefix}/tests/ground_truth.json")
                )
                assert _PRIVATE_TEXT not in instruction
                assert "no-network" in instruction
                assert "non-publishable" in instruction
                assert _PRIVATE_TEXT not in dockerfile
                assert _PRIVATE_TEXT not in json.dumps(public_task)
                assert set(public_task) == {
                    "schema_version",
                    "task_id",
                    "source",
                    "dataset_revision",
                    "scorer_revision",
                }
                assert set(public_task["source"]) == {
                    "runtime_path",
                    "sha256",
                    "size",
                    "page",
                }
                assert public_task["schema_version"] == PARSEBENCH_TASK_SCHEMA_VERSION
                assert public_task["task_id"] == row["task_id"]
                assert public_task["source"] == {
                    "runtime_path": ground_truth["source"]["runtime_path"],
                    "sha256": row["source_sha256"],
                    "size": row["source_size"],
                    "page": ground_truth["source"]["page"],
                }
                assert public_task["dataset_revision"] == row["source_revision"]
                assert public_task["scorer_revision"] == row["scorer_revision"]
                assert public_scope == verifier_scope
                assert public_scope == row["benchmark_scope"]
                assert public_scope["schema_version"] == PARSEBENCH_SCOPE_SCHEMA_VERSION
                assert public_scope["publishable"] is False
                assert (
                    public_scope["selection_manifest_sha256"]
                    == result_one.selection_manifest_sha256
                )
                assert (
                    f"COPY {PARSEBENCH_TASK_FILENAME} {PARSEBENCH_TASK_RUNTIME_PATH}"
                    in dockerfile
                )
                assert (
                    f"COPY {PARSEBENCH_SCOPE_FILENAME} {PARSEBENCH_SCOPE_RUNTIME_PATH}"
                    in dockerfile
                )
                assert (
                    f'--scope "$script_dir/{PARSEBENCH_SCOPE_FILENAME}"'
                    in verifier_script
                )
                assert "dimensions" not in json.dumps(public_scope)
                assert "rule_count" not in json.dumps(public_scope)
                assert "COPY --chmod=755 test.sh /tests/test.sh" in verifier_dockerfile
                assert (
                    "COPY --chmod=444 ground_truth.json /tests/ground_truth.json"
                    in verifier_dockerfile
                )
                assert "COPY input/ /workspace/input/" in verifier_dockerfile
                assert verifier_source_bytes == environment_source_bytes
                assert (
                    "sha256:" + sha256(verifier_source_bytes).hexdigest()
                    == ground_truth["source"]["sha256"]
                )
                assert (
                    ground_truth["source"]["runtime_path"]
                    == "/workspace/input/" + Path(verifier_source).name
                )
                assert task_config["artifacts"] == ["/logs/artifacts"]
                assert task_config["verifier"]["environment_mode"] == "separate"
                assert (
                    task_config["verifier"]["environment"]["network_mode"]
                    == "no-network"
                )
                assert task_config["environment"]["network_mode"] == "no-network"
                assert "docker_image" not in task_config["environment"]
                assert "image" not in task_config["environment"]
                assert "/workspace/output" not in instruction
                assert "/workspace/output" not in task_toml_text
                assert "/workspace/output" not in verifier_script
                for artifact_name in ("result.json", "document.md", "layout.json"):
                    artifact_path = f"/logs/artifacts/{artifact_name}"
                    assert artifact_path in instruction
                    assert artifact_path in verifier_script
                assert (
                    ground_truth["schema_version"]
                    == "aworld-parsebench-ground-truth/v1"
                )
                assert ground_truth["task_id"] == row["task_id"]
                assert ground_truth["source"]["sha256"] == row["source_sha256"]
                assert ground_truth["rules"]
                assert _PRIVATE_TEXT in json.dumps(ground_truth)
