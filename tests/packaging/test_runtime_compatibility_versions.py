from __future__ import annotations

import ast
import importlib.util
from pathlib import Path
import tomllib
from zipfile import ZipFile, ZipInfo

import aworld
import pytest


ROOT = Path(__file__).resolve().parents[2]
RUNTIME_CLI_METADATA = ROOT / "packaging/runtime/aworld-cli.pyproject.toml"
BUILD_SCRIPT = ROOT / "scripts/build_runtime_compatibility_wheels.py"


def _literal_assignment(path: Path, name: str) -> str:
    module = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for statement in module.body:
        if not isinstance(statement, ast.Assign):
            continue
        if any(
            isinstance(target, ast.Name) and target.id == name
            for target in statement.targets
        ):
            value = ast.literal_eval(statement.value)
            assert isinstance(value, str)
            return value
    raise AssertionError(f"{name} is not assigned in {path}")


def _load_build_script():
    spec = importlib.util.spec_from_file_location("runtime_wheel_builder", BUILD_SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_runtime_compatibility_versions_and_dependency_are_explicit() -> None:
    core_version = _literal_assignment(ROOT / "aworld/version_gen.py", "__version__")
    metadata = tomllib.loads(RUNTIME_CLI_METADATA.read_text(encoding="utf-8"))

    assert core_version == "0.2.13"
    assert metadata["project"]["version"] == "0.1.5"
    assert f"aworld=={core_version}" in metadata["project"]["dependencies"]


def test_runtime_compatibility_manifest_keeps_full_cli_entrypoint() -> None:
    metadata = tomllib.loads(RUNTIME_CLI_METADATA.read_text(encoding="utf-8"))

    assert metadata["project"]["scripts"] == {"aworld-cli": "aworld_cli.main:main"}
    assert metadata["tool"]["hatch"]["build"]["targets"]["wheel"]["packages"] == [
        "src/aworld_cli"
    ]


def test_core_source_checkout_ignores_unrelated_installed_metadata(monkeypatch) -> None:
    class StaleDistribution:
        version = "0.2.8"

        @staticmethod
        def locate_file(path: str) -> Path:
            return Path("/unrelated/site-packages") / path

    monkeypatch.setattr(aworld, "distribution", lambda _name: StaleDistribution())

    assert aworld._resolve_version() == "1.0.0a4"


def test_installed_core_uses_its_own_distribution_metadata(monkeypatch) -> None:
    class CurrentDistribution:
        version = "0.2.13"

        @staticmethod
        def locate_file(_path: str) -> Path:
            return Path(aworld.__file__)

    monkeypatch.setattr(aworld, "_is_source_checkout", lambda: False)
    monkeypatch.setattr(aworld, "distribution", lambda _name: CurrentDistribution())

    assert aworld._resolve_version() == "0.2.13"


def test_runtime_builder_requires_an_explicit_reproducible_epoch() -> None:
    builder = _load_build_script()

    with pytest.raises(ValueError, match="SOURCE_DATE_EPOCH"):
        builder._source_date_epoch(None)


def test_runtime_builder_rejects_timestamp_drift_without_hash_timing_tricks(
    tmp_path,
) -> None:
    builder = _load_build_script()
    epoch = 1791176179
    wheel = tmp_path / "fixture.whl"
    expected = ZipInfo("package.py", date_time=builder._zip_datetime(epoch))
    with ZipFile(wheel, "w") as archive:
        archive.writestr(expected, "pass\n")
    builder._verify_wheel_timestamp(wheel, epoch)

    drifted = tmp_path / "drifted.whl"
    wrong = ZipInfo("package.py", date_time=(2026, 10, 5, 8, 59, 12))
    with ZipFile(drifted, "w") as archive:
        archive.writestr(wrong, "pass\n")
    with pytest.raises(RuntimeError, match="timestamps outside SOURCE_DATE_EPOCH"):
        builder._verify_wheel_timestamp(drifted, epoch)


def test_runtime_builder_preserves_tracked_nested_dist_assets(tmp_path) -> None:
    builder = _load_build_script()
    source = tmp_path / "source"
    destination = tmp_path / "staged"
    (source / "packaging/runtime").mkdir(parents=True)
    (source / "aworld-cli").mkdir()
    webui_dist = source / "aworld/cmd/web/webui/dist"
    webui_dist.mkdir(parents=True)
    (source / "pyproject.toml").write_text("[project]\nname='fixture'\n")
    (source / builder.CLI_MANIFEST).write_text("[project]\nname='fixture-cli'\n")
    (source / "aworld-cli/pyproject.toml").write_text("stale\n")
    (webui_dist / "index.html").write_text("tracked bundle\n")
    (source / "dist").mkdir()
    (source / "dist/stale.whl").write_text("release output\n")

    builder._stage_source(source, destination)

    assert (destination / "aworld/cmd/web/webui/dist/index.html").read_text() == (
        "tracked bundle\n"
    )
    assert not (destination / "dist").exists()
