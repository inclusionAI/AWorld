import importlib.util
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
BOOTSTRAP_PATH = REPO_ROOT / "aworld-cli" / "src" / "aworld_cli" / "_path_bootstrap.py"


def _load_bootstrap_module():
    assert BOOTSTRAP_PATH.exists(), f"missing bootstrap module: {BOOTSTRAP_PATH}"
    spec = importlib.util.spec_from_file_location("aworld_cli_path_bootstrap", BOOTSTRAP_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_repo_aworld_path_is_prioritized_ahead_of_other_editable_install():
    module = _load_bootstrap_module()

    repo_root = REPO_ROOT
    package_file = repo_root / "aworld-cli" / "src" / "aworld_cli" / "__init__.py"
    other_install = "/Users/wuman/Documents/workspace/aworld-mas/aworld"

    paths = [
        "/Users/wuman/miniconda3/bin",
        "/Users/wuman/miniconda3/lib/python3.12/site-packages",
        str(repo_root / "aworld-cli" / "src"),
        other_install,
    ]

    updated = module.prioritize_repo_aworld_path(paths, str(package_file))

    assert updated[0] == str(repo_root)
    assert updated.index(str(repo_root)) < updated.index(other_install)


def test_no_repo_aworld_inserted_when_sibling_package_missing(tmp_path):
    module = _load_bootstrap_module()

    fake_package_file = tmp_path / "aworld-cli" / "src" / "aworld_cli" / "__init__.py"
    fake_package_file.parent.mkdir(parents=True)
    fake_package_file.write_text("")

    original = ["/Users/wuman/miniconda3/bin", "/tmp/site-packages"]
    updated = module.prioritize_repo_aworld_path(list(original), str(fake_package_file))

    assert updated == original
