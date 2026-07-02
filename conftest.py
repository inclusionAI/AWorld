from __future__ import annotations

import sys
from pathlib import Path


_REPO_ROOT = Path(__file__).resolve().parent
_CLI_SRC = _REPO_ROOT / "aworld-cli" / "src"

for candidate in (str(_CLI_SRC), str(_REPO_ROOT)):
    if candidate in sys.path:
        sys.path.remove(candidate)
    sys.path.insert(0, candidate)
