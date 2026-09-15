from __future__ import annotations

import ast
from pathlib import Path


# These files are the terminal-integration subagent's reserved work area.  The
# subset assertion prevents any new production dependency while allowing that
# concurrent change to delete the remaining references without touching this
# test.
_PENDING_TERMINAL_INTEGRATION = {
    "aworld-cli/src/aworld_cli/builtin_agents/smllc/agents/aworld_agent.py",
    "aworld-cli/src/aworld_cli/builtin_agents/smllc/agents/audio/mcp_config.py",
    "aworld-cli/src/aworld_cli/builtin_agents/smllc/agents/avatar/mcp_config.py",
    "aworld-cli/src/aworld_cli/builtin_agents/smllc/agents/developer/mcp_config.py",
    "aworld-cli/src/aworld_cli/builtin_agents/smllc/agents/diffusion/mcp_config.py",
    "aworld-cli/src/aworld_cli/builtin_agents/smllc/agents/evaluator/mcp_config.py",
    "aworld-cli/src/aworld_cli/builtin_agents/smllc/agents/image/mcp_config.py",
}


def _production_python_files(root: Path) -> list[Path]:
    return sorted(
        (
            *root.joinpath("aworld").rglob("*.py"),
            *root.joinpath("aworld-cli", "src", "aworld_cli").rglob("*.py"),
        )
    )


def _depends_on_examples(path: Path) -> bool:
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("examples"):
            return True
        if isinstance(node, ast.Import) and any(
            alias.name.startswith("examples") for alias in node.names
        ):
            return True
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if node.value.startswith("examples."):
                return True
    return False


def test_production_code_has_no_unowned_example_package_dependency() -> None:
    root = Path(__file__).resolve().parents[2]
    dependencies = {
        str(path.relative_to(root))
        for path in _production_python_files(root)
        if _depends_on_examples(path)
    }

    assert dependencies <= _PENDING_TERMINAL_INTEGRATION
