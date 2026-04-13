from dataclasses import dataclass
from pathlib import Path
import shutil

DEMO_ROOT_MARKER = ".cron_experience_demo_root"


def _validate_demo_path_invariants(paths: "DemoPaths") -> None:
    root = paths.root.resolve()
    aworld_dir = paths.aworld_dir.resolve()
    outputs_dir = paths.outputs_dir.resolve()
    cron_store = paths.cron_store.resolve()

    expected_aworld_dir = root / ".aworld"
    expected_outputs_dir = root / "outputs"
    expected_cron_store = expected_aworld_dir / "cron.json"

    if aworld_dir != expected_aworld_dir:
        raise ValueError("aworld_dir must equal root/.aworld")
    if outputs_dir != expected_outputs_dir:
        raise ValueError("outputs_dir must equal root/outputs")
    if cron_store != expected_cron_store:
        raise ValueError("cron_store must equal aworld_dir/cron.json")


@dataclass(frozen=True)
class DemoPaths:
    root: Path
    aworld_dir: Path
    outputs_dir: Path
    cron_store: Path

    @classmethod
    def from_root(cls, root: Path) -> "DemoPaths":
        resolved_root = root.resolve()
        aworld_dir = resolved_root / ".aworld"
        outputs_dir = resolved_root / "outputs"
        cron_store = aworld_dir / "cron.json"
        return cls(
            root=resolved_root,
            aworld_dir=aworld_dir,
            outputs_dir=outputs_dir,
            cron_store=cron_store,
        )


def ensure_demo_paths(paths: DemoPaths, reset: bool = False) -> None:
    _validate_demo_path_invariants(paths)

    marker_path = paths.aworld_dir / DEMO_ROOT_MARKER

    if reset and paths.root.exists():
        if not marker_path.is_file():
            raise ValueError("reset=True requires an initialized demo root marker")
        shutil.rmtree(paths.root)

    paths.root.mkdir(parents=True, exist_ok=True)
    paths.aworld_dir.mkdir(parents=True, exist_ok=True)
    paths.outputs_dir.mkdir(parents=True, exist_ok=True)
    marker_path.write_text("aworld-cron-experience-demo\n", encoding="utf-8")
