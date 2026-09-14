import subprocess
import sys


def test_builtin_bootstrap_registers_async_mcp() -> None:
    """Sandbox tools advertised to a model must also be executable by the runner."""

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import aworld.tools; "
                "aworld.tools.ensure_builtin_tools_registered(); "
                "from aworld.core.tool.base import ToolFactory; "
                "assert 'async_mcp' in ToolFactory._cls"
            ),
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
