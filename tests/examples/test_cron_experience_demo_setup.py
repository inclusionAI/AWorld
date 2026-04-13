from pathlib import Path

import pytest

from examples.cron_experience_demo.demo_setup import DemoPaths, ensure_demo_paths


def test_ensure_demo_paths_creates_isolated_dirs(tmp_path: Path) -> None:
    root = tmp_path / "cron-demo"
    paths = DemoPaths.from_root(root)

    ensure_demo_paths(paths)

    assert paths.root == root.resolve()
    assert paths.root.is_dir()
    assert paths.aworld_dir.is_dir()
    assert paths.outputs_dir.is_dir()
    assert paths.cron_store.parent == paths.aworld_dir


def test_ensure_demo_paths_reset_clears_existing_content(tmp_path: Path) -> None:
    root = tmp_path / "cron-demo"
    paths = DemoPaths.from_root(root)
    ensure_demo_paths(paths)

    stale_file = paths.outputs_dir / "stale.txt"
    stale_file.write_text("old-data", encoding="utf-8")
    assert stale_file.exists()

    ensure_demo_paths(paths, reset=True)

    assert paths.root.is_dir()
    assert paths.aworld_dir.is_dir()
    assert paths.outputs_dir.is_dir()
    assert not stale_file.exists()


def test_ensure_demo_paths_reset_rejects_paths_outside_root(tmp_path: Path) -> None:
    root = tmp_path / "cron-demo"
    outside = tmp_path / "outside"
    paths = DemoPaths(
        root=root.resolve(),
        aworld_dir=outside.resolve() / ".aworld",
        outputs_dir=outside.resolve() / "outputs",
        cron_store=outside.resolve() / ".aworld" / "cron.json",
    )

    with pytest.raises(ValueError, match="must equal"):
        ensure_demo_paths(paths, reset=True)


def test_ensure_demo_paths_reset_rejects_uninitialized_root(tmp_path: Path) -> None:
    root = tmp_path / "unsafe-root"
    root.mkdir()
    (root / ".aworld").mkdir()
    (root / "outputs").mkdir()

    paths = DemoPaths(
        root=root.resolve(),
        aworld_dir=(root / ".aworld").resolve(),
        outputs_dir=(root / "outputs").resolve(),
        cron_store=(root / ".aworld" / "cron.json").resolve(),
    )

    with pytest.raises(ValueError, match="marker"):
        ensure_demo_paths(paths, reset=True)


def test_ensure_demo_paths_reset_rejects_nested_descendant_paths_even_with_marker(
    tmp_path: Path,
) -> None:
    root = tmp_path / "unsafe-root"
    nested_aworld = root / "nested" / ".aworld"
    nested_outputs = root / "nested" / "outputs"

    nested_aworld.mkdir(parents=True)
    nested_outputs.mkdir(parents=True)
    (nested_aworld / ".cron_experience_demo_root").write_text(
        "aworld-cron-experience-demo\n", encoding="utf-8"
    )

    paths = DemoPaths(
        root=root.resolve(),
        aworld_dir=nested_aworld.resolve(),
        outputs_dir=nested_outputs.resolve(),
        cron_store=(nested_aworld / "cron.json").resolve(),
    )

    with pytest.raises(ValueError, match="must equal"):
        ensure_demo_paths(paths, reset=True)
