from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import tarfile
import zipfile

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
CLI_ROOT = REPO_ROOT / "aworld-cli"


def _build_distribution(project_root: Path, output: Path, target: str) -> Path:
    # The test uses the already installed build backend and never resolves deps.
    pytest.importorskip("hatchling.build")
    output.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            f"from hatchling.build import build_{target}; "
            f"print(build_{target}({str(output)!r}))",
        ],
        cwd=project_root,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    return output / result.stdout.strip().splitlines()[-1]


def _assert_bundled_filex(wheel: Path) -> None:
    with zipfile.ZipFile(wheel) as archive:
        root = "aworld_cli/builtin_skills/"
        manifest = json.loads(archive.read(root + "manifest.json"))
        assert manifest["schema_version"] == "aworld.builtin-skills/v1"
        assert manifest["skills"]["filex"]["source"] == "aworld-skills/filex"
        source_root = REPO_ROOT / "aworld-skills" / "filex"
        for source in source_root.rglob("*"):
            if source.is_file() and "__pycache__" not in source.parts and source.suffix != ".pyc":
                relative = source.relative_to(source_root).as_posix()
                assert archive.read(root + "filex/" + relative) == source.read_bytes()


def test_cli_wheel_bundles_filex_and_loads_it_outside_the_checkout(tmp_path: Path) -> None:
    wheel = _build_distribution(CLI_ROOT, tmp_path / "wheel", "wheel")
    _assert_bundled_filex(wheel)
    installed = tmp_path / "installed"
    with zipfile.ZipFile(wheel) as archive:
        archive.extractall(installed)

    # Changing the imported package path simulates an installed wheel while the
    # framework dependencies remain available in the test environment.
    code = """
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from aworld_cli.core.builtin_skills import get_builtin_skills_path
from aworld_cli.core.skill_activation_resolver import SkillActivationResolver, SkillResolverRequest
root = get_builtin_skills_path()
assert root == Path(sys.argv[1]) / 'aworld_cli' / 'builtin_skills', root
result = SkillActivationResolver().resolve(SkillResolverRequest(
    plugin_roots=(), runtime_scope='session', task_text='Summarize the attached PDF'))
assert result.active_skill_names == ('filex',), result
assert Path(result.skill_configs['filex']['asset_root']) == root / 'filex'
assert (root / 'filex/scripts/filex.py').is_file()
"""
    subprocess.run(
        [sys.executable, "-c", code, str(installed)],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
    )


def test_cli_sdist_can_build_a_wheel_without_repository_skill_sources(tmp_path: Path) -> None:
    sdist = _build_distribution(CLI_ROOT, tmp_path / "sdist", "sdist")
    extracted = tmp_path / "unpacked"
    with tarfile.open(sdist) as archive:
        archive.extractall(extracted, filter="data")
    project_root = next(extracted.iterdir())
    assert (project_root / "hatch_build.py").is_file()
    assert not (project_root.parent / "aworld-skills").exists()
    wheel = _build_distribution(project_root, tmp_path / "rebuilt-wheel", "wheel")
    _assert_bundled_filex(wheel)
