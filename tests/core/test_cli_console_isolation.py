from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[2]


def test_rich_cli_import_removes_standard_console_handlers() -> None:
    environment = dict(os.environ)
    environment.pop("AWORLD_DISABLE_CONSOLE_LOG", None)
    environment["PYTHONPATH"] = os.pathsep.join(
        (str(ROOT), str(ROOT / "aworld-cli" / "src"))
    )
    script = """
import json, logging, os, sys
root = logging.getLogger()
root.handlers.clear()
root.addHandler(logging.StreamHandler(sys.stderr))
import aworld_cli.main
aworld_logger = logging.getLogger('aworld')
def console_count(logger):
    return sum(
        isinstance(handler, logging.StreamHandler)
        and not isinstance(handler, logging.FileHandler)
        for handler in logger.handlers
    )
print(json.dumps({
    'disable_console': os.environ.get('AWORLD_DISABLE_CONSOLE_LOG'),
    'root_console_handlers': console_count(root),
    'aworld_console_handlers': console_count(aworld_logger),
    'aworld_propagates': aworld_logger.propagate,
}))
"""

    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=ROOT,
        env=environment,
        text=True,
        capture_output=True,
        timeout=20,
        check=True,
    )

    assert completed.stderr == ""
    assert json.loads(completed.stdout) == {
        "disable_console": "true",
        "root_console_handlers": 0,
        "aworld_console_handlers": 0,
        "aworld_propagates": False,
    }
