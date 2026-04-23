from __future__ import annotations

import json
import sys

from .stdio_harness import local_server_env, repo_root
from .validation import run_phase1_validation_against_stdio_command
from .validation_profiles import SELF_TEST_PHASE1_PROFILE


async def run_self_test() -> int:
    root = repo_root()
    payload = await run_phase1_validation_against_stdio_command(
        command=[sys.executable, "-m", "aworld_cli.main", "--no-banner", "acp"],
        cwd=root,
        env=local_server_env(extra_env={"AWORLD_ACP_SELF_TEST_BRIDGE": "1"}),
        profile=SELF_TEST_PHASE1_PROFILE,
        session_params={"cwd": ".", "mcpServers": []},
    )
    sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
    sys.stdout.flush()
    return 0 if payload["ok"] else 1
